"""
routes/profile_routes.py
-------------------------
DPP profile routes:
  GET  /profile/spotify/playlists  — list playlists (sample when no Premium)
  POST /profile/analyse/spotify   — Step 1: design signals + slider predictions
  POST /profile/build/spotify     — Step 2: build DPP from Spotify + answers

  ── Analyse only (Step 1 of 2) ──────────────────────────────────────────────
  POST /profile/analyse/images  — run Template 19/20 on uploaded images,
                                   return per-image analysis + question + options.
                                   No DPP built yet.
  POST /profile/analyse/boards  — same but downloads pin images from selected boards.

  ── Build (Step 2 of 2) ─────────────────────────────────────────────────────
  POST /profile/build/images    — accepts analyses + user checkbox answers,
                                   builds DPP and enriches via Template 16.
  POST /profile/build/boards    — same for Pinterest boards.
  GET  /profile/build           — Pinterest boards -> raw DPP -> AI enrichment
                                   (legacy one-shot flow, no question step).

  ── Utility ─────────────────────────────────────────────────────────────────
  GET    /profile/get     — return stored DPP
  GET    /profile/boards  — list Pinterest boards for selection UI
  DELETE /profile/clear   — reset
"""

import base64
import json as _json
import logging
import os
from typing import List, Optional

import requests
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from config import Config
from modules.dpp_builder import build_dpp_from_images, build_dpp_from_pinterest
from modules.gemini_ai import (
    analyse_single_image_vanilla,
    analyse_single_image_with_questions,
    enrich_dpp_with_ai,
    predict_material_shape_dna,
    predict_material_shape_dna_spotify,
    spotify_mood_to_attributes,
    spotify_mood_vector,
    spotify_question_from_signals,
)
from modules.image_analyser import ImageData, analyse_images as _analyse_images
from modules.spotify_auth import get_valid_spotify_token
from modules.spotify_api import (
    build_user_summary_from_live,
    fetch_user_playlists,
)
from modules.pinterest_fetcher import (
    PinterestAuthError,
    PinterestPermissionError,
    get_all_boards_with_pins,
    get_boards,
    get_user_profile,
)

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGES = 10
MAX_MB = 5


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _read_and_validate(files: List[UploadFile]) -> tuple:
    """Read UploadFile bytes and validate type/size. Returns (List[ImageData], skipped_msgs)."""
    valid, skipped = [], []
    for f in files[:MAX_IMAGES]:
        ct = f.content_type or ""
        if ct not in ALLOWED_TYPES:
            skipped.append(f"{f.filename}: unsupported type")
            continue
        content = await f.read()
        size_mb = len(content) / (1024 * 1024)
        if size_mb > MAX_MB:
            skipped.append(f"{f.filename}: too large ({size_mb:.1f} MB)")
            continue
        valid.append(ImageData(filename=f.filename, content=content, content_type=ct))
    return valid, skipped


def _enrich_and_store(dpp: dict, session: dict) -> dict:
    """Run AI enrichment on a raw DPP, store in session dict, return enriched DPP."""
    logger.info("Running AI enrichment on DPP...")
    try:
        dpp = enrich_dpp_with_ai(dpp)
    except Exception as e:
        logger.warning(f"AI enrichment skipped: {e}")
    try:
        safe = dict(dpp)
        if isinstance(safe.get("board_summary"), list):
            safe["board_summary"] = [
                {"name": b.get("name"), "pin_count": b.get("pin_count", 0), "image_url": None}
                for b in safe["board_summary"]
            ]
        session["dpp"] = safe
    except Exception:
        session["dpp"] = {
            "profile_version": dpp.get("profile_version"),
            "source": dpp.get("source"),
        }
    return dpp


def _build_user_selections_string(selections: list) -> str:
    """
    Convert frontend checkbox payload into a readable string for Template 16.
    """
    parts = []
    for i, s in enumerate(selections or [], start=1):
        fname = s.get("filename", f"image {i}")
        checked = [c for c in (s.get("checked") or []) if c]
        other = (s.get("other") or "").strip()
        if other:
            checked.append(f"Other: {other}")
        if checked:
            parts.append(f"Image {i} ({fname}): {', '.join(checked)}.")
    return " ".join(parts)


def _aggregate_image_signals(analyses: list) -> dict:
    """
    Merge per-image analysis dicts into one aggregated signal dict
    suitable for Template 21 (DNA Predictor).
    """
    styles, materials, mood_tags, colours = set(), set(), set(), []
    density_counts = {}
    for a in analyses:
        for s in a.get("styles", []):
            styles.add(s)
        for m in a.get("materials", []):
            materials.add(m)
        for t in a.get("mood_tags", []):
            mood_tags.add(t)
        for c in a.get("dominant_colours", [])[:2]:
            colours.append(c)
        d = a.get("spatial_density", "moderate")
        density_counts[d] = density_counts.get(d, 0) + 1
    dominant_density = max(density_counts, key=density_counts.get) if density_counts else "moderate"
    return {
        "styles": list(styles)[:8],
        "materials": list(materials)[:10],
        "mood_tags": list(mood_tags)[:8],
        "colours": colours[:6],
        "spatial_density": dominant_density,
    }


def _download_board_images(selected_boards: list, max_per_board: int = 4) -> tuple:
    """
    Download up to max_per_board pin images from each selected board.
    Returns (List[ImageData], preview_list).
    """
    image_data_list, preview_list = [], []
    for b in selected_boards:
        count = 0
        for p in b.get("pins", [])[:12]:
            if count >= max_per_board:
                break
            url = p.get("image_url")
            if not url:
                continue
            try:
                r = requests.get(url, timeout=10)
                if not r.ok:
                    continue
                content = r.content
                if len(content) > MAX_MB * 1024 * 1024:
                    logger.debug(f"Skipping large pin image {url}")
                    continue
                fname = url.split("?")[0].rstrip("/").split("/")[-1] or f"pin_{p.get('id')}.jpg"
                mtype = r.headers.get("content-type", "image/jpeg")
                image_data_list.append(ImageData(filename=fname, content=content, content_type=mtype))
                preview_list.append({"name": fname, "image_url": url})
                count += 1
            except Exception as e:
                logger.debug(f"Failed to download pin image {url}: {e}")
    return image_data_list, preview_list


# ── POST /profile/analyse/images ──────────────────────────────────────────────

@router.post("/analyse/images")
async def analyse_images_for_questions(
    request: Request,
    images: List[UploadFile] = File(...),
):
    """Step 1 of 2 — image upload flow. Runs Template 19/20 per image."""
    if not images or all(f.filename == "" for f in images):
        return JSONResponse({"error": "Please upload at least one image."}, status_code=400)

    valid, skipped = await _read_and_validate(images)
    if not valid:
        return JSONResponse({"error": "No valid images.", "skipped": skipped}, status_code=400)

    template_id = Config.IMAGE_QUESTION_TEMPLATE_ID
    logger.info(f"Analysing {len(valid)} images with Template {template_id} (question mode)...")

    analyses = []
    for img in valid:
        result = analyse_single_image_with_questions(
            img.filename, img.content, img.content_type, template_id
        )
        try:
            b64 = base64.b64encode(img.content).decode()
            result["image_url"] = f"data:{img.content_type};base64,{b64}"
        except Exception:
            result["image_url"] = None
        analyses.append(result)

    slider_predictions = {}
    try:
        aggregated = _aggregate_image_signals(analyses)
        slider_predictions = predict_material_shape_dna(aggregated)
    except Exception as e:
        logger.warning(f"DNA slider prediction skipped: {e}")

    return {
        "success":            True,
        "analyses":           analyses,
        "skipped":            skipped,
        "template_used":      template_id,
        "slider_predictions": slider_predictions,
    }


# ── POST /profile/analyse/boards ──────────────────────────────────────────────

@router.post("/analyse/boards")
async def analyse_boards_for_questions(request: Request):
    """Step 1 of 2 — Pinterest boards flow."""
    if not request.session.get("pinterest_connected"):
        return JSONResponse({"error": "Pinterest not connected.", "reconnect": True}, status_code=401)

    data      = await request.json()
    board_ids = data.get("board_ids") or []
    if not isinstance(board_ids, list) or not board_ids:
        return JSONResponse({"error": "Please provide board_ids as a non-empty list."}, status_code=400)

    token = request.session.get("pinterest_access_token")
    try:
        all_boards = get_all_boards_with_pins(token, max_boards=25, max_pins_per_board=50)
    except Exception as e:
        return JSONResponse({"error": f"Could not fetch boards: {e}"}, status_code=500)

    selected = [b for b in all_boards if b.get("id") in board_ids]
    if not selected:
        return JSONResponse({"error": "No matching boards found for given board_ids."}, status_code=400)

    image_data_list, preview_list = _download_board_images(selected, max_per_board=4)
    if not image_data_list:
        return JSONResponse({"error": "No images could be downloaded from selected boards."}, status_code=500)

    template_id = Config.IMAGE_QUESTION_TEMPLATE_ID
    logger.info(f"Analysing {len(image_data_list)} board images with Template {template_id}...")

    analyses = []
    for img, preview in zip(image_data_list, preview_list):
        result = analyse_single_image_with_questions(img.filename, img.content, img.content_type, template_id)
        result["image_url"] = preview.get("image_url")
        analyses.append(result)

    slider_predictions = {}
    try:
        aggregated = _aggregate_image_signals(analyses)
        slider_predictions = predict_material_shape_dna(aggregated)
    except Exception as e:
        logger.warning(f"DNA slider prediction skipped: {e}")

    return {
        "success":            True,
        "analyses":           analyses,
        "template_used":      template_id,
        "slider_predictions": slider_predictions,
    }


# ── POST /profile/build/images ────────────────────────────────────────────────

@router.post("/build/images")
async def build_profile_images(
    request:       Request,
    images:        List[UploadFile] = File(...),
    selections:    Optional[str]    = Form("[]"),
    slider_values: Optional[str]    = Form("{}"),
):
    """Step 2 of 2 — image upload flow."""
    if not images or all(f.filename == "" for f in images):
        return JSONResponse({"error": "Please upload at least one image."}, status_code=400)

    valid, skipped = await _read_and_validate(images)
    if not valid:
        return JSONResponse({"error": "No valid images.", "skipped": skipped}, status_code=400)

    try:
        sel_list    = _json.loads(selections    or "[]")
    except Exception:
        sel_list    = []
    try:
        slider_dict = _json.loads(slider_values or "{}")
    except Exception:
        slider_dict = {}

    logger.info(
        f"Building DPP from {len(valid)} images. "
        f"Selections: {bool(sel_list)} | Sliders: {bool(slider_dict)}"
    )

    try:
        analyses = _analyse_images(valid)
    except Exception as e:
        return JSONResponse({"error": f"Image analysis failed: {e}"}, status_code=500)

    try:
        previews = []
        for img in valid[:len(analyses)]:
            try:
                previews.append({
                    "name":      img.filename,
                    "image_url": f"data:{img.content_type};base64,{base64.b64encode(img.content).decode()}",
                })
            except Exception:
                continue

        raw_dpp = build_dpp_from_images(analyses)
        raw_dpp["board_summary"]  = previews
        raw_dpp["image_analyses"] = analyses

        if sel_list:
            raw_dpp["user_selections"] = _build_user_selections_string(sel_list)
        if slider_dict:
            raw_dpp["material_shape_dna"] = {
                "material_dna": slider_dict.get("material_dna", {}),
                "shape_dna":    slider_dict.get("shape_dna", {}),
                "source":       "ai_predicted+user_adjusted",
            }

        ok     = raw_dpp.get("images_analyzed", 0)
        failed = raw_dpp.get("images_failed", 0)
        dpp    = _enrich_and_store(raw_dpp, request.session)

        msg = f"Profile built from {ok} image{'s' if ok != 1 else ''}."
        if failed:
            msg += f" ({failed} failed.)"
        return {"success": True, "profile": dpp, "message": msg, "skipped": skipped}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── POST /profile/build/boards ────────────────────────────────────────────────

@router.post("/build/boards")
async def build_profile_from_selected_boards(request: Request):
    """Step 2 of 2 — Pinterest boards flow."""
    if not request.session.get("pinterest_connected"):
        return JSONResponse({"error": "Pinterest not connected.", "reconnect": True}, status_code=401)

    data        = await request.json()
    board_ids   = data.get("board_ids") or []
    selections  = data.get("selections") or []
    slider_dict = data.get("slider_values") or {}

    if not isinstance(board_ids, list) or not board_ids:
        return JSONResponse({"error": "Please provide board_ids as a non-empty list."}, status_code=400)

    token = request.session.get("pinterest_access_token")
    try:
        all_boards = get_all_boards_with_pins(token, max_boards=25, max_pins_per_board=50)
    except Exception as e:
        return JSONResponse({"error": f"Could not fetch boards: {e}"}, status_code=500)

    selected = [b for b in all_boards if b.get("id") in board_ids]
    if not selected:
        return JSONResponse({"error": "No matching boards found for given board_ids."}, status_code=400)

    image_data_list, preview_list = _download_board_images(selected, max_per_board=4)
    if not image_data_list:
        return JSONResponse({"error": "No images could be downloaded or analysed."}, status_code=500)

    analyses = []
    for img in image_data_list:
        try:
            ana = analyse_single_image_vanilla(img.filename, img.content, img.content_type)
            analyses.append(ana)
        except Exception as e:
            logger.debug(f"Image analysis failed for {img.filename}: {e}")

    if not analyses:
        return JSONResponse({"error": "All image analyses failed."}, status_code=500)

    try:
        raw_dpp = build_dpp_from_images(analyses)
        raw_dpp["source_boards"]  = [b.get("name") for b in selected]
        raw_dpp["board_summary"]  = preview_list
        raw_dpp["image_analyses"] = analyses

        if selections:
            raw_dpp["user_selections"] = _build_user_selections_string(selections)
        if slider_dict:
            raw_dpp["material_shape_dna"] = {
                "material_dna": slider_dict.get("material_dna", {}),
                "shape_dna":    slider_dict.get("shape_dna", {}),
                "source":       "ai_predicted+user_adjusted",
            }

        dpp = _enrich_and_store(raw_dpp, request.session)
        return {
            "success": True,
            "profile": dpp,
            "message": f"Profile built from {len(analyses)} analysed images.",
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── GET /profile/build (legacy Pinterest one-shot) ────────────────────────────

@router.get("/build")
async def build_profile(request: Request):
    """Legacy one-shot Pinterest DPP build — no question step."""
    if not request.session.get("pinterest_connected"):
        return JSONResponse({"error": "Pinterest not connected.", "reconnect": True}, status_code=401)

    token = request.session.get("pinterest_access_token")
    mode  = request.session.get("pinterest_mode", "oauth")
    logger.info(f"Building DPP via Pinterest — mode: {mode}")

    try:
        user_info = get_user_profile(token)
        boards = get_all_boards_with_pins(token, max_boards=5, max_pins_per_board=20)

    except PinterestPermissionError as e:
        logger.error(f"403: {e}")
        return JSONResponse({
            "error": (
                "Pinterest trial access cannot read boards/pins. "
                "Go to developers.pinterest.com/apps -> click 'Upgrade access' "
                "and request Standard access. While waiting, use the image upload option instead."
            ),
            "error_type": "trial_access",
        }, status_code=403)

    except PinterestAuthError as e:
        logger.warning(f"401: {e}")
        if mode == "direct_token":
            return JSONResponse({
                "error":      "Token expired. Generate a new one from developers.pinterest.com/apps.",
                "error_type": "token_expired",
                "reconnect":  True,
            }, status_code=401)
        new_token = _try_refresh(request.session)
        if not new_token:
            return JSONResponse({"error": "Session expired. Please reconnect.", "reconnect": True}, status_code=401)
        try:
            user_info = get_user_profile(new_token)
            boards    = get_all_boards_with_pins(new_token, max_boards=5, max_pins_per_board=20)
        except PinterestPermissionError:
            return JSONResponse({"error": "Trial access restriction. Use image upload instead.", "error_type": "trial_access"}, status_code=403)
        except Exception as e2:
            return JSONResponse({"error": str(e2), "reconnect": True}, status_code=500)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        raw_dpp = build_dpp_from_pinterest(boards)
        raw_dpp["pinterest_user"] = request.session.get("pinterest_user") or user_info
        dpp = _enrich_and_store(raw_dpp, request.session)
        return {
            "success": True,
            "profile": dpp,
            "message": (
                f"Profile built from {raw_dpp.get('boards_analyzed', 0)} boards "
                f"and {raw_dpp.get('pins_analyzed', 0)} pins."
            ),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── GET /profile/get ──────────────────────────────────────────────────────────

@router.get("/get")
async def get_profile(request: Request):
    dpp = request.session.get("dpp")
    if not dpp:
        raise HTTPException(status_code=404, detail="No profile yet.")
    return {"profile": dpp}


# ── GET /profile/boards ───────────────────────────────────────────────────────

@router.get("/boards")
async def list_boards(request: Request):
    """Return a lightweight list of the user's Pinterest boards for the selection UI."""
    if not request.session.get("pinterest_connected"):
        return JSONResponse({"error": "Pinterest not connected.", "reconnect": True}, status_code=401)
    token = request.session.get("pinterest_access_token")
    try:
        boards = get_boards(token, max_boards=50)
        return {"boards": [
            {"id": b.get("id"), "name": b.get("name"), "pin_count": b.get("pin_count"), "image_url": b.get("image_url")}
            for b in boards
        ]}
    except PinterestAuthError as e:
        return JSONResponse({"error": str(e), "reconnect": True}, status_code=401)
    except PinterestPermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── GET /profile/spotify/playlists ────────────────────────────────────────────

def _load_sample_spotify_data():
    """Load sample Spotify JSON. Returns (playlists, data). data has playlists + optional user_summary / top_genres / audio_features."""
    path = os.path.join(os.path.dirname(__file__), "..", "data", "sample_spotify_playlists.json")
    if not os.path.exists(path):
        return [], {}
    with open(path, encoding="utf-8") as f:
        data = _json.load(f)
    playlists = data.get("playlists", [])
    return playlists, data


@router.get("/spotify/playlists")
async def list_spotify_playlists(request: Request):
    """Return playlists: live from Spotify API when connected, else sample data."""
    if not request.session.get("spotify_connected"):
        return JSONResponse({"error": "Spotify not connected.", "reconnect": True}, status_code=401)

    token = get_valid_spotify_token(request.session)
    fallback_reason = None

    if not token:
        fallback_reason = "no_token"
        logger.warning("Spotify playlists: no valid token in session.")
    else:
        try:
            live, api_status = fetch_user_playlists(token)
            if api_status == 403:
                return JSONResponse({
                    "error":     "Spotify denied access. Add your account in the Spotify Developer Dashboard.",
                    "reconnect": False,
                }, status_code=403)
            if live:
                return {"playlists": live, "source": "spotify_api"}
            fallback_reason = "empty_response" if api_status is None else f"http_{api_status}"
            logger.warning("Spotify playlists: API returned no playlists (status=%s).", api_status)
        except Exception as e:
            fallback_reason = "api_error"
            logger.warning(f"Spotify API playlists failed: {e}")

    playlists, _ = _load_sample_spotify_data()
    lightweight  = [
        {"id": p.get("id"), "name": p.get("name"), "description": p.get("description"), "track_count": len(p.get("tracks", []))}
        for p in playlists
    ]
    return {"playlists": lightweight, "source": "sample", "fallback_reason": fallback_reason}


# ── POST /profile/analyse/spotify ─────────────────────────────────────────────

def _build_spotify_user_summary_params(playlists, selected_playlists, data):
    """
    Build (top_artists, top_tracks, top_genres, audio_features) for Template 24.
    Prefer user_summary from data; else derive from selected_playlists.
    """
    us = data.get("user_summary") or {}
    # Prefer user_summary; fallback to root-level (sample file may have top_genres/audio_features at root)
    top_artists_list = us.get("top_artists") or data.get("top_artists")
    top_tracks_list = us.get("top_tracks") or data.get("top_tracks")
    top_genres_list = us.get("top_genres") or data.get("top_genres")
    audio_features_obj = us.get("audio_features") or data.get("audio_features")

    if top_artists_list or top_tracks_list or top_genres_list:
        top_artists = ", ".join(top_artists_list[:15]) if isinstance(top_artists_list, list) else str(top_artists_list or "")
        if isinstance(top_tracks_list, list):
            top_tracks = "; ".join(
                f"{t.get('name', '')} – {t.get('artist', '')}" if isinstance(t, dict) else str(t)
                for t in top_tracks_list[:20]
            )
        else:
            top_tracks = str(top_tracks_list or "")
        top_genres = ", ".join(top_genres_list[:15]) if isinstance(top_genres_list, list) else str(top_genres_list or "")
        audio_features_str = _json.dumps(audio_features_obj) if audio_features_obj else "not provided"
        return top_artists, top_tracks, top_genres, audio_features_str

    # Derive from selected playlists
    artists = set()
    tracks_lines = []
    genres = set()
    for p in selected_playlists:
        for t in p.get("tracks", [])[:15]:
            artists.add(t.get("artist", ""))
            genres.add(t.get("genre", ""))
            tracks_lines.append(f"{t.get('name', '')} – {t.get('artist', '')}" + (f" ({t.get('genre', '')})" if t.get("genre") else ""))
    top_artists = ", ".join(sorted(artists - {""})[:15]) or "unknown"
    top_tracks = "\n".join(tracks_lines[:25]) or "unknown"
    top_genres = ", ".join(sorted(genres - {""})[:15]) or "unknown"
    audio_features_str = "not provided"
    return top_artists, top_tracks, top_genres, audio_features_str


def _spotify_signal_to_analysis(signals: dict, playlist_names: str = "Spotify playlists") -> dict:
    """Turn Template 25 output into one 'analysis' object for question card + build_dpp_from_images.

    Uses Template 26 (spotify_question_from_signals) to generate a playlist-aware question and options.
    Falls back to a simple fixed question if Template 26 fails.
    """
    styles = signals.get("styles", [])[:4]
    mood_tags = signals.get("mood_tags", [])
    # Default question/options (fallback)
    question = "Based on your playlists and music, which design direction speaks to you most?"
    options = [f"The {s} vibe" for s in styles] if styles else [
        "Calm and minimal",
        "Warm and layered",
        "Bold and expressive",
        "Natural and organic",
    ]
    try:
        q_res = spotify_question_from_signals(styles, mood_tags, playlist_names)
        q_text = (q_res or {}).get("question") or ""
        q_opts = (q_res or {}).get("options") or []
        if q_text.strip() and q_opts:
            question = q_text.strip()
            options = q_opts[:4]
    except Exception:
        # Keep fallback
        pass
    return {
        "filename": "Spotify playlists",
        "image_url": "",
        "styles": signals.get("styles", []),
        "dominant_colours": signals.get("colours", []) or signals.get("dominant_colours", []),
        "materials": signals.get("materials", []),
        "mood_tags": signals.get("mood_tags", []),
        "spatial_density": signals.get("spatial_density", "moderate"),
        "confidence": float(signals.get("confidence", 0.8)),
        "question": question,
        "options": options[:4],
    }


@router.post("/analyse/spotify")
async def analyse_spotify(request: Request):
    """Step 1 of 2 — Spotify flow. Uses Template 24 → 25 → 23."""
    if not request.session.get("spotify_connected"):
        return JSONResponse({"error": "Spotify not connected.", "reconnect": True}, status_code=401)

    data         = await request.json()
    playlist_ids = data.get("playlist_ids") or []
    token        = get_valid_spotify_token(request.session)

    if token and playlist_ids:
        # Live API: fetch tracks and audio features for selected playlists
        try:
            live_playlists, _ = fetch_user_playlists(token)
            id_to_name = {p["id"]: p["name"] for p in live_playlists if p.get("id") and p.get("name")}
            playlist_names = [id_to_name.get(pid, "") for pid in playlist_ids if id_to_name.get(pid) is not None]
            if not playlist_names:
                playlist_names = [id_to_name.get(pid, f"Playlist {pid}") for pid in playlist_ids]
            result, live_error = build_user_summary_from_live(token, playlist_ids, playlist_names)
            if live_error:
                return jsonify({"error": live_error}), 403 if "Forbidden" in live_error else 400
            if result:
                top_artists, top_tracks, top_genres, audio_features_str, top_genres_list = result
                try:
                    mood_vector = spotify_mood_vector(top_artists, top_tracks, top_genres, audio_features_str)
                    signals     = spotify_mood_to_attributes(mood_vector)
                except Exception as e:
                    logger.exception(e)
                    return JSONResponse({"error": f"Design analysis failed: {e}"}, status_code=500)
                analysis = _spotify_signal_to_analysis(signals, ", ".join(playlist_names))
                slider_predictions = {}
                try:
                    slider_predictions = predict_material_shape_dna_spotify({
                        "styles": signals.get("styles", []), "materials": signals.get("materials", []),
                        "colours": signals.get("colours", []) or signals.get("dominant_colours", []),
                        "mood_tags": signals.get("mood_tags", []), "spatial_density": signals.get("spatial_density", "moderate"),
                    })
                except Exception as e:
                    logger.warning(f"Spotify DNA prediction skipped: {e}")
                request.session["spotify_analyses"]          = [analysis]
                request.session["spotify_slider_predictions"] = slider_predictions
                request.session["spotify_playlist_ids"]      = playlist_ids
                request.session["spotify_playlist_names"]    = playlist_names
                request.session["spotify_mood_vector"]       = mood_vector
                request.session["spotify_top_genres"]        = top_genres_list
                return {"success": True, "analyses": [analysis], "slider_predictions": slider_predictions, "playlist_ids": playlist_ids, "source": "spotify_api"}
        except Exception as e:
            logger.warning(f"Spotify live analyse failed, falling back to sample: {e}")

    playlists, spotify_data = _load_sample_spotify_data()
    if not playlists:
        return JSONResponse({"error": "No playlists available. Connect Spotify and select playlists."}, status_code=500)

    selected = [p for p in playlists if p.get("id") in playlist_ids] if playlist_ids else playlists
    if not selected:
        return JSONResponse({"error": "No playlists selected."}, status_code=400)

    top_artists, top_tracks, top_genres, audio_features_str = _build_spotify_user_summary_params(playlists, selected, spotify_data)

    try:
        mood_vector = spotify_mood_vector(top_artists, top_tracks, top_genres, audio_features_str)
        signals     = spotify_mood_to_attributes(mood_vector)
    except Exception as e:
        logger.exception(e)
        return JSONResponse({"error": f"Design analysis failed: {e}"}, status_code=500)

    playlist_names = [p.get("name", "") for p in selected if p.get("name")]
    analysis       = _spotify_signal_to_analysis(signals, ", ".join(playlist_names) or "Spotify playlists")

    slider_predictions = {}
    try:
        slider_predictions = predict_material_shape_dna_spotify({
            "styles": signals.get("styles", []), "materials": signals.get("materials", []),
            "colours": signals.get("colours", []) or signals.get("dominant_colours", []),
            "mood_tags": signals.get("mood_tags", []), "spatial_density": signals.get("spatial_density", "moderate"),
        })
    except Exception as e:
        logger.warning(f"Spotify DNA prediction skipped: {e}")

    us = spotify_data.get("user_summary") or {}
    request.session["spotify_analyses"]          = [analysis]
    request.session["spotify_slider_predictions"] = slider_predictions
    request.session["spotify_playlist_ids"]      = [p.get("id") for p in selected]
    request.session["spotify_playlist_names"]    = playlist_names
    request.session["spotify_mood_vector"]       = mood_vector
    request.session["spotify_top_genres"]        = us.get("top_genres") or spotify_data.get("top_genres") or []

    return {"success": True, "analyses": [analysis], "slider_predictions": slider_predictions, "playlist_ids": request.session["spotify_playlist_ids"]}


# ── POST /profile/build/spotify ───────────────────────────────────────────────

@router.post("/build/spotify")
async def build_profile_spotify(request: Request):
    """Step 2 of 2 — Spotify flow. Build DPP from stored Spotify analysis + selections + sliders."""
    if not request.session.get("spotify_connected"):
        return JSONResponse({"error": "Spotify not connected.", "reconnect": True}, status_code=401)

    analyses = request.session.get("spotify_analyses") or []
    if not analyses:
        return JSONResponse({"error": "Run analyse/spotify first (Step 1).", "reconnect": False}, status_code=400)

    data        = await request.json()
    selections  = data.get("selections") or []
    slider_dict = data.get("slider_values") or {}

    try:
        raw_dpp = build_dpp_from_images(analyses)
        raw_dpp["source"]             = "spotify"
        playlist_names                = request.session.get("spotify_playlist_names") or []
        raw_dpp["board_summary"]      = [{"name": n, "pin_count": 0, "image_url": ""} for n in (playlist_names or ["Spotify playlists"])]
        raw_dpp["image_analyses"]     = analyses
        raw_dpp["spotify_top_genres"] = request.session.get("spotify_top_genres") or []
        mood_vector = request.session.get("spotify_mood_vector")
        if mood_vector:
            raw_dpp["mood_vector"] = mood_vector

        if selections:
            raw_dpp["user_selections"] = _build_user_selections_string(selections)
        if slider_dict:
            raw_dpp["material_shape_dna"] = {
                "material_dna": slider_dict.get("material_dna", {}),
                "shape_dna":    slider_dict.get("shape_dna", {}),
                "source":       "ai_predicted+user_adjusted",
            }

        dpp = _enrich_and_store(raw_dpp, request.session)
        dpp["source"]        = "spotify"
        dpp["board_summary"] = raw_dpp.get("board_summary") or [{"name": "Spotify playlists", "pin_count": 0, "image_url": ""}]
        if mood_vector:
            dpp["mood_vector"] = mood_vector

        for key in ("spotify_analyses", "spotify_slider_predictions", "spotify_playlist_ids",
                    "spotify_playlist_names", "spotify_mood_vector", "spotify_top_genres"):
            request.session.pop(key, None)

        return {"success": True, "profile": dpp, "message": "Profile built from your Spotify taste."}
    except Exception as e:
        logger.exception(e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── DELETE /profile/clear ─────────────────────────────────────────────────────

@router.delete("/clear")
async def clear_profile(request: Request):
    request.session.pop("dpp", None)
    return {"message": "Profile cleared."}


# ── Private ───────────────────────────────────────────────────────────────────

def _try_refresh(session: dict):
    from modules.pinterest_auth import refresh_access_token
    rt = session.get("pinterest_refresh_token")
    if not rt:
        return None
    try:
        new = refresh_access_token(rt)
        session["pinterest_access_token"]  = new["access_token"]
        session["pinterest_refresh_token"] = new.get("refresh_token", rt)
        return new["access_token"]
    except Exception as e:
        logger.error(f"Pinterest refresh failed: {e}")
        return None
