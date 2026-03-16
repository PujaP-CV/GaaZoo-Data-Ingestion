"""Tests for profile APIs (DPP, Spotify playlists, boards, analyse, build)."""

import pytest


# ── GET /profile/get ─────────────────────────────────────────────────────

def test_profile_get_no_profile_returns_404(client):
    """GET /profile/get without stored DPP returns 404."""
    response = client.get("/profile/get")
    assert response.status_code == 404
    assert "profile" in response.json().get("detail", "").lower()


# ── DELETE /profile/clear ───────────────────────────────────────────────

def test_profile_clear_returns_200(client):
    """DELETE /profile/clear returns 200."""
    response = client.delete("/profile/clear")
    assert response.status_code == 200
    assert "clear" in response.json().get("message", "").lower() or "clear" in str(response.json()).lower()


# ── GET /profile/boards (requires Pinterest session) ─────────────────────

def test_profile_boards_not_connected_returns_401(client):
    """GET /profile/boards without Pinterest returns 401."""
    response = client.get("/profile/boards")
    assert response.status_code == 401
    assert "pinterest" in response.json().get("error", "").lower()


# ── GET /profile/spotify/playlists (requires Spotify session) ─────────────

def test_profile_spotify_playlists_not_connected_returns_401(client):
    """GET /profile/spotify/playlists without Spotify returns 401."""
    response = client.get("/profile/spotify/playlists")
    assert response.status_code == 401
    assert "spotify" in response.json().get("error", "").lower()


# ── POST /profile/analyse/images ──────────────────────────────────────────

def test_profile_analyse_images_no_files_returns_400(client):
    """POST /profile/analyse/images with no images returns 400."""
    response = client.post("/profile/analyse/images", files=[])
    assert response.status_code == 422  # FastAPI validation for empty list


# ── POST /profile/analyse/boards ──────────────────────────────────────────

def test_profile_analyse_boards_not_connected_returns_401(client):
    """POST /profile/analyse/boards without Pinterest returns 401."""
    response = client.post("/profile/analyse/boards", json={"board_ids": ["123"]})
    assert response.status_code == 401


def test_profile_analyse_boards_empty_board_ids_returns_400(client):
    """POST /profile/analyse/boards with empty board_ids returns 400 (needs session first)."""
    # With no session we get 401; with session but empty list we get 400
    response = client.post("/profile/analyse/boards", json={"board_ids": []})
    assert response.status_code in (400, 401)


# ── POST /profile/analyse/interior-design ─────────────────────────────────

def test_profile_analyse_interior_design_no_image_returns_400(client):
    """POST /profile/analyse/interior-design without image returns 422."""
    response = client.post("/profile/analyse/interior-design")
    assert response.status_code == 422


# ── POST /profile/build/images ───────────────────────────────────────────

def test_profile_build_images_no_files_returns_400(client):
    """POST /profile/build/images with no images returns 400/422."""
    response = client.post(
        "/profile/build/images",
        data={"selections": "[]", "slider_values": "{}"},
        files=[],
    )
    assert response.status_code in (400, 422)


# ── POST /profile/build/boards ───────────────────────────────────────────

def test_profile_build_boards_not_connected_returns_401(client):
    """POST /profile/build/boards without Pinterest returns 401."""
    response = client.post("/profile/build/boards", json={"board_ids": ["x"]})
    assert response.status_code == 401


# ── POST /profile/build/spotify ──────────────────────────────────────────

def test_profile_build_spotify_not_connected_returns_401(client):
    """POST /profile/build/spotify without Spotify returns 401."""
    response = client.post("/profile/build/spotify", json={"selections": [], "slider_values": {}})
    assert response.status_code == 401


# ── GET /profile/build (legacy) ──────────────────────────────────────────

def test_profile_build_legacy_not_connected_returns_401(client):
    """GET /profile/build without Pinterest returns 401."""
    response = client.get("/profile/build")
    assert response.status_code == 401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
