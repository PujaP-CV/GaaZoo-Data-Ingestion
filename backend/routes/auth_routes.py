"""
routes/auth_routes.py
----------------------
Pinterest:
  POST /auth/pinterest/token    — paste token directly (trial/testing)
  GET  /auth/pinterest/login    — OAuth flow (needs approved app)
  GET  /auth/pinterest/callback — OAuth callback
Spotify:
  GET  /auth/spotify/login      — OAuth flow
  GET  /auth/spotify/callback   — OAuth callback
  GET  /auth/status             — connection status (pinterest + spotify)
  POST /auth/logout             — disconnect all
  POST /auth/spotify/disconnect — disconnect Spotify only
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from modules.pinterest_auth import build_auth_url, exchange_code_for_token
from modules.pinterest_fetcher import get_user_profile, PinterestAuthError
from modules.spotify_auth import (
    build_auth_url as spotify_build_auth_url,
    exchange_code_for_token as spotify_exchange_code,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _fe(path: str, pinterest: bool = False) -> str:
    """
    Build a redirect URL pointing to the frontend.
    - Spotify/default  : uses FRONTEND_URL        (127.0.0.1:3000)
    - Pinterest        : uses PINTEREST_FRONTEND_URL (localhost:3000)
      Pinterest only accepts localhost redirect URIs, so its post-auth
      redirect must also land on localhost to match the session cookie.
    """
    from config import Config
    base = (Config.PINTEREST_FRONTEND_URL if pinterest else Config.FRONTEND_URL).rstrip("/")
    return f"{base}/{path.lstrip('/')}"


# ── Direct token ─────────────────────────────────────────────────────────────

@router.post("/pinterest/token")
async def pinterest_direct_token(request: Request):
    body  = await request.json()
    token = (body.get("access_token") or "").strip()
    if not token:
        return JSONResponse({"error": "access_token is required"}, status_code=400)

    try:
        user_info = get_user_profile(token)
    except PinterestAuthError as e:
        return JSONResponse({
            "error": str(e),
            "hint": (
                "The generated token needs all 3 scopes: boards:read, pins:read, "
                "user_accounts:read. On the app dashboard, select all scopes before "
                "clicking Generate token."
            ),
        }, status_code=401)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    request.session["pinterest_access_token"]  = token
    request.session["pinterest_refresh_token"] = None
    request.session["pinterest_connected"]     = True
    request.session["pinterest_mode"]          = "direct_token"
    request.session["pinterest_user"]          = user_info

    logger.info(f"Direct token connected: @{user_info.get('username')}")
    return {
        "success":  True,
        "mode":     "direct_token",
        "username": user_info.get("username"),
        "message":  f"Connected as @{user_info.get('username')}",
    }


# ── Pinterest OAuth ───────────────────────────────────────────────────────────

@router.get("/pinterest/login")
async def pinterest_login(request: Request):
    auth_url, state = build_auth_url()
    request.session["pinterest_oauth_state"] = state
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/pinterest/callback")
async def pinterest_callback(request: Request):
    params = request.query_params

    if params.get("state", "") != request.session.get("pinterest_oauth_state", ""):
        return RedirectResponse(url=_fe("?error=pinterest_state_mismatch", pinterest=True), status_code=302)

    error = params.get("error")
    if error:
        return RedirectResponse(url=_fe(f"?error={params.get('error_description', error)}", pinterest=True), status_code=302)

    code = params.get("code")
    if not code:
        return RedirectResponse(url=_fe("?error=no_code", pinterest=True), status_code=302)

    try:
        token_data = exchange_code_for_token(code)
    except Exception as e:
        logger.error(f"Pinterest token exchange failed: {e}")
        return RedirectResponse(url=_fe("?error=token_exchange_failed", pinterest=True), status_code=302)

    request.session["pinterest_access_token"]  = token_data.get("access_token")
    request.session["pinterest_refresh_token"] = token_data.get("refresh_token")
    request.session["pinterest_connected"]     = True
    request.session["pinterest_mode"]          = "oauth"

    logger.info("Pinterest OAuth connected successfully")
    return RedirectResponse(url=_fe("?pinterest_connected=true", pinterest=True), status_code=302)


# ── Spotify OAuth ─────────────────────────────────────────────────────────────

@router.get("/spotify/login")
async def spotify_login(request: Request):
    auth_url, state = spotify_build_auth_url()
    request.session["spotify_oauth_state"] = state
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/spotify/callback")
async def spotify_callback(request: Request):
    params = request.query_params

    if params.get("state", "") != request.session.get("spotify_oauth_state", ""):
        return RedirectResponse(url=_fe("?error=spotify_state_mismatch"), status_code=302)

    error = params.get("error")
    if error:
        return RedirectResponse(url=_fe(f"?error={params.get('error_description', error)}"), status_code=302)

    code = params.get("code")
    if not code:
        return RedirectResponse(url=_fe("?error=no_code"), status_code=302)

    try:
        token_data = spotify_exchange_code(code)
    except Exception as e:
        logger.error(f"Spotify token exchange failed: {e}")
        return RedirectResponse(url=_fe("?error=spotify_token_exchange_failed"), status_code=302)

    request.session["spotify_access_token"]  = token_data.get("access_token")
    request.session["spotify_refresh_token"] = token_data.get("refresh_token")
    request.session["spotify_connected"]     = True

    logger.info("Spotify OAuth connected successfully")
    return RedirectResponse(url=_fe("?spotify_connected=true"), status_code=302)


# ── Status / logout ───────────────────────────────────────────────────────────

@router.get("/status")
async def auth_status(request: Request):
    pinterest_connected = (
        request.session.get("pinterest_connected", False)
        and bool(request.session.get("pinterest_access_token"))
    )
    user = request.session.get("pinterest_user", {})
    spotify_connected = (
        request.session.get("spotify_connected", False)
        and bool(request.session.get("spotify_access_token"))
    )
    return {
        "pinterest_connected": pinterest_connected,
        "mode":                request.session.get("pinterest_mode", ""),
        "username":            user.get("username", "") if isinstance(user, dict) else "",
        "spotify_connected":   spotify_connected,
    }


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"message": "Disconnected"}


@router.post("/spotify/disconnect")
async def spotify_disconnect(request: Request):
    for key in ("spotify_access_token", "spotify_refresh_token", "spotify_connected"):
        request.session.pop(key, None)
    return {"message": "Spotify disconnected"}
