"""
modules/pinterest_fetcher.py
-----------------------------
Fetches boards and pins from Pinterest API v5.
"""

import requests
from flask import current_app


class PinterestAuthError(Exception):
    """401 — token invalid/expired or missing scope."""
    pass

class PinterestPermissionError(Exception):
    """403 — trial restriction or insufficient app permissions."""
    pass


def _get(endpoint: str, access_token: str, params: dict = None) -> dict:
    base    = current_app.config["PINTEREST_API_BASE"]
    url     = f"{base}{endpoint}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=15)
    except requests.exceptions.ConnectionError:
        raise Exception("Cannot reach Pinterest API.")
    except requests.exceptions.Timeout:
        raise Exception("Pinterest API timed out.")

    current_app.logger.info(f"Pinterest {endpoint} -> {r.status_code}: {r.text[:200]}")

    if r.status_code == 200:
        data = r.json()
        if not isinstance(data, dict):
            raise Exception(f"Unexpected Pinterest response: {str(data)[:100]}")
        return data

    try:
        err = r.json()
        msg = (err.get("message") or err.get("error_description") or err.get("error") or str(err)) if isinstance(err, dict) else str(err)[:200]
    except Exception:
        msg = r.text[:200]

    if r.status_code == 401:
        raise PinterestAuthError(f"Pinterest 401: {msg}. Missing scope? Need: boards:read, pins:read, user_accounts:read")
    if r.status_code == 403:
        raise PinterestPermissionError(f"Pinterest 403: {msg}. Trial access restriction — request Standard access.")

    raise Exception(f"Pinterest API {r.status_code} on {endpoint}: {msg}")


def get_user_profile(access_token: str) -> dict:
    data = _get("/user_account", access_token)

    # FIX: Pinterest v5 returns profile_image as a plain string URL, not a dict
    profile_img = data.get("profile_image", "")
    if isinstance(profile_img, dict):
        profile_img = profile_img.get("medium") or profile_img.get("large") or ""
    elif not isinstance(profile_img, str):
        profile_img = ""

    return {
        "username":       data.get("username", ""),
        "profile_image":  profile_img,
        "website":        data.get("website_url", ""),
        "bio":            data.get("about", ""),
        "follower_count": data.get("follower_count", 0),
    }


def get_boards(access_token: str, max_boards: int = 25) -> list[dict]:
    data = _get("/boards", access_token, params={"page_size": max_boards})
    boards = []
    for item in data.get("items", []):
        boards.append({
            "id":          item.get("id", ""),
            "name":        item.get("name", ""),
            "description": item.get("description", ""),
            "pin_count":   item.get("pin_count", 0),
            "image_url":   _board_image(item),
        })
    return boards


def get_pins_for_board(access_token: str, board_id: str, max_pins: int = 50) -> list[dict]:
    data = _get(f"/boards/{board_id}/pins", access_token, params={"page_size": max_pins})
    pins = []
    for item in data.get("items", []):
        img = _pin_image(item)
        if not img:
            continue
        pins.append({
            "id":          item.get("id", ""),
            "title":       item.get("title", ""),
            "description": item.get("description", ""),
            "image_url":   img,
            "link":        item.get("link", ""),
        })
    return pins


def get_all_boards_with_pins(access_token: str, max_boards: int = 5, max_pins_per_board: int = 20) -> list[dict]:
    boards = get_boards(access_token, max_boards=max_boards)
    for board in boards:
        try:
            board["pins"] = get_pins_for_board(access_token, board["id"], max_pins=max_pins_per_board)
        except Exception as e:
            board["pins"] = []
            board["fetch_error"] = str(e)
    return boards


def _board_image(item: dict) -> str:
    media = item.get("media", {})
    if not isinstance(media, dict): return ""
    url = media.get("image_cover_url", "")
    if url and isinstance(url, str): return url
    thumbs = media.get("pin_thumbnail_urls", [])
    return thumbs[0] if thumbs else ""


def _pin_image(item: dict) -> str:
    media  = item.get("media", {})
    if not isinstance(media, dict): return ""
    images = media.get("images", {})
    if not isinstance(images, dict): return ""
    for size in ["600x", "400x300", "236x", "150x150"]:
        entry = images.get(size, {})
        if isinstance(entry, dict) and entry.get("url"):
            return entry["url"]
    return ""