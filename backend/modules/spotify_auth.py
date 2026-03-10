"""
modules/spotify_auth.py
------------------------
Handles Spotify OAuth 2.0 Authorization Code flow:
  - Build the authorization URL
  - Exchange code for access token
  - Refresh access token
"""

import secrets
import requests
import base64
from urllib.parse import urlencode
from flask import current_app, session


def build_auth_url(state: str = None) -> tuple[str, str]:
    """
    Build the Spotify OAuth authorization URL.

    Returns:
        (auth_url, state) — state is a random string used to prevent CSRF.
    """
    if state is None:
        state = secrets.token_urlsafe(32)

    cfg = current_app.config

    params = {
        "client_id": cfg["SPOTIFY_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": cfg["SPOTIFY_REDIRECT_URI"],
        "scope": cfg["SPOTIFY_SCOPE"],
        "state": state,
    }

    query = urlencode(params)
    auth_url = f"{cfg['SPOTIFY_AUTH_URL']}?{query}"

    return auth_url, state


def exchange_code_for_token(code: str) -> dict:
    """
    Exchange the authorization code for an access token.

    Args:
        code: The authorization code from Spotify callback.

    Returns:
        Token dict containing access_token, refresh_token, expires_in, token_type.

    Raises:
        Exception if the exchange fails.
    """
    cfg = current_app.config

    credentials = f"{cfg['SPOTIFY_CLIENT_ID']}:{cfg['SPOTIFY_CLIENT_SECRET']}"
    encoded = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg["SPOTIFY_REDIRECT_URI"],
    }

    response = requests.post(
        cfg["SPOTIFY_TOKEN_URL"],
        headers=headers,
        data=payload,
        timeout=10,
    )

    if response.status_code != 200:
        raise Exception(
            f"Spotify token exchange failed: {response.status_code} — {response.text}"
        )

    return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    """
    Refresh the Spotify access token using the refresh token.

    Returns:
        Token dict with access_token, expires_in, token_type. May include refresh_token.
    """
    cfg = current_app.config
    credentials = f"{cfg['SPOTIFY_CLIENT_ID']}:{cfg['SPOTIFY_CLIENT_SECRET']}"
    encoded = base64.b64encode(credentials.encode()).decode()
    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    response = requests.post(
        cfg["SPOTIFY_TOKEN_URL"],
        headers=headers,
        data=payload,
        timeout=10,
    )
    if response.status_code != 200:
        raise Exception(
            f"Spotify token refresh failed: {response.status_code} — {response.text}"
        )
    return response.json()


def get_valid_spotify_token():
    """
    Return a valid Spotify access token from session, refreshing if necessary.
    Updates session with new token if refreshed.
    Returns None if not connected or refresh fails.
    """
    token = session.get("spotify_access_token")
    refresh = session.get("spotify_refresh_token")
    if not token and not refresh:
        return None
    if token:
        # We don't track expiry here; try using token and refresh on 401 if needed
        return token
    if refresh:
        try:
            data = refresh_access_token(refresh)
            new_token = data.get("access_token")
            if new_token:
                session["spotify_access_token"] = new_token
                if data.get("refresh_token"):
                    session["spotify_refresh_token"] = data["refresh_token"]
                session.modified = True
                return new_token
        except Exception:
            pass
    return None
