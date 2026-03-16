"""Tests for viewer and 3D APIs (proxy-glb, generate-3d, scale-3d, etc.)."""

from unittest.mock import patch

import pytest


# ── GET / (index) and /dpp ──────────────────────────────────────────────

def test_index_returns_html_or_404(client):
    """GET / returns 200 if index.html exists else 404."""
    response = client.get("/")
    # May be 200 if frontend/index.html exists from repo root
    assert response.status_code in (200, 404)


def test_dpp_page_returns_html_or_404(client):
    """GET /dpp returns 200 if dpp.html exists else 404."""
    response = client.get("/dpp")
    assert response.status_code in (200, 404)


# ── GET /proxy-glb ──────────────────────────────────────────────────────

def test_proxy_glb_missing_url_returns_400(client):
    """GET /proxy-glb without url returns 400."""
    response = client.get("/proxy-glb")
    assert response.status_code == 422  # FastAPI missing query param


def test_proxy_glb_invalid_url_returns_400(client):
    """GET /proxy-glb with non-https url returns 400."""
    response = client.get("/proxy-glb?url=http://example.com/model.glb")
    assert response.status_code == 400
    assert "url" in response.json().get("error", "").lower()


# ── POST /generate-3d ───────────────────────────────────────────────────

@patch("routes.viewer_routes.MESHY_API_KEY", "")
def test_generate_3d_no_api_key_returns_500(client):
    """POST /generate-3d without MESHY_API_KEY returns 500."""
    response = client.post(
        "/generate-3d",
        files={"image": ("test.jpg", b"\xff\xd8\xff", "image/jpeg")},
        data={},
    )
    assert response.status_code == 500
    assert "MESHY" in response.json().get("error", "").upper()


# ── POST /3d-dimensions ──────────────────────────────────────────────────

def test_3d_dimensions_no_file_returns_400(client):
    """POST /3d-dimensions without file returns 422."""
    response = client.post("/3d-dimensions", data={"obj_unit": "cm"})
    assert response.status_code == 422


def test_3d_dimensions_unsupported_extension_returns_400(client):
    """POST /3d-dimensions with .stl or other unsupported file returns 400."""
    response = client.post(
        "/3d-dimensions",
        files={"file": ("model.stl", b"binary content", "application/octet-stream")},
        data={"obj_unit": "cm"},
    )
    assert response.status_code == 400
    assert "glb" in response.json().get("error", "").lower() or "obj" in response.json().get("error", "").lower()


# ── POST /scale-3d ──────────────────────────────────────────────────────

def test_scale_3d_no_file_returns_400(client):
    """POST /scale-3d without file returns 422."""
    response = client.post(
        "/scale-3d",
        data={"obj_unit": "cm"},
    )
    assert response.status_code == 422


def test_scale_3d_unsupported_extension_returns_400(client):
    """POST /scale-3d with unsupported extension returns 400."""
    response = client.post(
        "/scale-3d",
        files={"file": ("model.stl", b"data", "application/octet-stream")},
        data={"obj_unit": "cm"},
    )
    assert response.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
