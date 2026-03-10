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
  POST /auth/logout             — disconnect
"""

from flask import Blueprint, redirect, request, session, jsonify, current_app
from modules.pinterest_auth import build_auth_url, exchange_code_for_token
from modules.pinterest_fetcher import get_user_profile, PinterestAuthError
from modules.spotify_auth import build_auth_url as spotify_build_auth_url
from modules.spotify_auth import exchange_code_for_token as spotify_exchange_code

auth_bp = Blueprint("auth", __name__)


# ── Direct token ─────────────────────────────────────────────────────────────
@auth_bp.route("/pinterest/token", methods=["POST"])
def pinterest_direct_token():
    body  = request.get_json(silent=True) or {}
    token = body.get("access_token", "").strip()
    if not token:
        return jsonify({"error": "access_token is required"}), 400

    try:
        user_info = get_user_profile(token)
    except PinterestAuthError as e:
        return jsonify({
            "error": str(e),
            "hint": "The generated token needs all 3 scopes: boards:read, pins:read, user_accounts:read. "
                    "On the app dashboard, make sure to select all scopes before clicking Generate token."
        }), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    session["pinterest_access_token"]  = token
    session["pinterest_refresh_token"] = None
    session["pinterest_connected"]     = True
    session["pinterest_mode"]          = "direct_token"
    session["pinterest_user"]          = user_info
    session.permanent = True

    current_app.logger.info(f"Direct token connected: @{user_info.get('username')}")
    return jsonify({"success": True, "mode": "direct_token", "username": user_info.get("username"), "message": f"Connected as @{user_info.get('username')}"})


# ── OAuth login ───────────────────────────────────────────────────────────────
@auth_bp.route("/pinterest/login")
def pinterest_login():
    auth_url, state = build_auth_url()
    session["pinterest_oauth_state"] = state
    return redirect(auth_url)


@auth_bp.route("/pinterest/callback")
def pinterest_callback():
    if request.args.get("state", "") != session.get("pinterest_oauth_state", ""):
        return redirect("/?error=pinterest_state_mismatch")

    error = request.args.get("error")
    if error:
        return redirect(f"/?error={request.args.get('error_description', error)}")

    code = request.args.get("code")
    if not code:
        return redirect("/?error=no_code")

    try:
        token_data = exchange_code_for_token(code)
    except Exception as e:
        current_app.logger.error(f"Token exchange failed: {e}")
        return redirect("/?error=token_exchange_failed")

    session["pinterest_access_token"]  = token_data.get("access_token")
    session["pinterest_refresh_token"] = token_data.get("refresh_token")
    session["pinterest_connected"]     = True
    session["pinterest_mode"]          = "oauth"
    session.permanent = True

    current_app.logger.info("OAuth connected successfully")
    return redirect("/?pinterest_connected=true")


# ── Spotify OAuth ─────────────────────────────────────────────────────────────
@auth_bp.route("/spotify/login")
def spotify_login():
    auth_url, state = spotify_build_auth_url()
    session["spotify_oauth_state"] = state
    return redirect(auth_url)


def _frontend_redirect(path_query: str):
    """Redirect back to the frontend on the same host/port as this Flask server."""
    return redirect(f"/{path_query.lstrip('/')}")


@auth_bp.route("/spotify/callback")
def spotify_callback():
    saved_state = session.get("spotify_oauth_state", "")
    if request.args.get("state", "") != saved_state:
        # Usually: user opened app at localhost but Spotify redirect URI is 127.0.0.1 (or vice versa)
        return _frontend_redirect("?error=spotify_state_mismatch")

    error = request.args.get("error")
    if error:
        return _frontend_redirect(f"?error={request.args.get('error_description', error)}")

    code = request.args.get("code")
    if not code:
        return _frontend_redirect("?error=no_code")

    try:
        token_data = spotify_exchange_code(code)
    except Exception as e:
        current_app.logger.error(f"Spotify token exchange failed: {e}")
        return _frontend_redirect("?error=spotify_token_exchange_failed")

    session["spotify_access_token"] = token_data.get("access_token")
    session["spotify_refresh_token"] = token_data.get("refresh_token")
    session["spotify_connected"] = True
    session.permanent = True

    current_app.logger.info("Spotify connected successfully")
    return _frontend_redirect("?spotify_connected=true")


# ── Status / logout ───────────────────────────────────────────────────────────
@auth_bp.route("/status")
def auth_status():
    connected = session.get("pinterest_connected", False) and bool(session.get("pinterest_access_token"))
    user      = session.get("pinterest_user", {})
    spotify_connected = session.get("spotify_connected", False) and bool(session.get("spotify_access_token"))
    return jsonify({
        "pinterest_connected": connected,
        "mode":     session.get("pinterest_mode", ""),
        "username": user.get("username", "") if isinstance(user, dict) else "",
        "spotify_connected": spotify_connected,
    })


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Disconnected"})


@auth_bp.route("/spotify/disconnect", methods=["POST"])
def spotify_disconnect():
    session.pop("spotify_access_token", None)
    session.pop("spotify_refresh_token", None)
    session.pop("spotify_connected", None)
    return jsonify({"message": "Spotify disconnected"})