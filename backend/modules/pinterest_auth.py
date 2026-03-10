"""
modules/pinterest_auth.py
--------------------------
Handles all Pinterest OAuth 2.0 flow:
  - Build the authorization URL
  - Exchange code for access token
  - Refresh expired tokens
"""

import secrets
import requests
import base64
from flask import current_app


def build_auth_url(state: str = None) -> tuple[str, str]:
    """
    Build the Pinterest OAuth authorization URL.

    Returns:
        (auth_url, state) — state is a random string used to prevent CSRF.
    """
    if state is None:
        state = secrets.token_urlsafe(32)

    cfg = current_app.config

    params = {
        "client_id":     cfg["PINTEREST_APP_ID"],
        "redirect_uri":  cfg["PINTEREST_REDIRECT_URI"],
        "response_type": "code",
        "scope":         cfg["PINTEREST_SCOPE"],
        "state":         state,
    }

    # Build query string manually for clarity
    query = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"{cfg['PINTEREST_AUTH_URL']}?{query}"

    return auth_url, state


def exchange_code_for_token(code: str) -> dict:
    """
    Exchange the authorization code for an access token.

    Args:
        code: The authorization code from Pinterest callback.

    Returns:
        Token dict containing access_token, refresh_token, expires_in, token_type.
    
    Raises:
        Exception if the exchange fails.
    """
    cfg = current_app.config

    # Pinterest requires Basic Auth with app credentials
    credentials = f"{cfg['PINTEREST_APP_ID']}:{cfg['PINTEREST_APP_SECRET']}"
    encoded     = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type":  "application/x-www-form-urlencoded",
    }

    payload = {
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": cfg["PINTEREST_REDIRECT_URI"],
    }

    response = requests.post(
        cfg["PINTEREST_TOKEN_URL"],
        headers=headers,
        data=payload,
        timeout=10
    )

    if response.status_code != 200:
        raise Exception(
            f"Pinterest token exchange failed: {response.status_code} — {response.text}"
        )

    return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    """
    Refresh an expired Pinterest access token.

    Args:
        refresh_token: The refresh token from the original OAuth exchange.

    Returns:
        New token dict.
    """
    cfg = current_app.config

    credentials = f"{cfg['PINTEREST_APP_ID']}:{cfg['PINTEREST_APP_SECRET']}"
    encoded     = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type":  "application/x-www-form-urlencoded",
    }

    payload = {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }

    response = requests.post(
        cfg["PINTEREST_TOKEN_URL"],
        headers=headers,
        data=payload,
        timeout=10
    )

    if response.status_code != 200:
        raise Exception(f"Token refresh failed: {response.status_code} — {response.text}")

    return response.json()
