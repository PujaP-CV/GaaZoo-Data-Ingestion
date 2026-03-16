"""Tests for auth APIs (Pinterest, Spotify, status, logout)."""

import pytest


# ── GET /auth/status ────────────────────────────────────────────────────

def test_auth_status_returns_200(client):
    """GET /auth/status returns 200 and connection flags."""
    response = client.get("/auth/status")
    assert response.status_code == 200
    data = response.json()
    assert "pinterest_connected" in data
    assert "spotify_connected" in data
    assert "username" in data
    assert "mode" in data


# ── POST /auth/logout ───────────────────────────────────────────────────

def test_logout_returns_200(client):
    """POST /auth/logout returns 200 and message."""
    response = client.post("/auth/logout")
    assert response.status_code == 200
    assert "message" in response.json()
    assert "disconnect" in response.json()["message"].lower()


# ── POST /auth/pinterest/token ──────────────────────────────────────────

def test_pinterest_token_missing_access_token_returns_400(client):
    """POST /auth/pinterest/token without access_token returns 400."""
    response = client.post("/auth/pinterest/token", json={})
    assert response.status_code == 400
    assert "access_token" in response.json().get("error", "").lower()


def test_pinterest_token_empty_access_token_returns_400(client):
    """POST /auth/pinterest/token with empty access_token returns 400."""
    response = client.post("/auth/pinterest/token", json={"access_token": "   "})
    assert response.status_code == 400


# ── POST /auth/spotify/disconnect ────────────────────────────────────────

def test_spotify_disconnect_returns_200(client):
    """POST /auth/spotify/disconnect returns 200."""
    response = client.post("/auth/spotify/disconnect")
    assert response.status_code == 200
    assert "Spotify" in response.json().get("message", "")


# ── OAuth redirects (smoke test) ────────────────────────────────────────

def test_pinterest_login_redirects(client):
    """GET /auth/pinterest/login returns 302 redirect."""
    response = client.get("/auth/pinterest/login", follow_redirects=False)
    assert response.status_code == 302
    assert "location" in [h.lower() for h in response.headers]


def test_spotify_login_redirects(client):
    """GET /auth/spotify/login returns 302 redirect."""
    response = client.get("/auth/spotify/login", follow_redirects=False)
    assert response.status_code == 302
    assert "location" in [h.lower() for h in response.headers]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
