"""Tests for Shapely layout validation and blueprint APIs."""


import pytest


# ── POST /shapely/validate-layout ────────────────────────────────────────

def test_validate_layout_no_file_returns_422(client):
    """POST /shapely/validate-layout without file returns 422."""
    response = client.post("/shapely/validate-layout")
    assert response.status_code == 422


def test_validate_layout_non_json_file_returns_400(client):
    """POST /shapely/validate-layout with non-JSON file returns 400."""
    response = client.post(
        "/shapely/validate-layout",
        files={"file": ("layout.txt", b"not json", "text/plain")},
    )
    assert response.status_code == 400
    assert "json" in response.json().get("detail", "").lower()


def test_validate_layout_invalid_json_returns_400(client):
    """POST /shapely/validate-layout with invalid JSON returns 400."""
    response = client.post(
        "/shapely/validate-layout",
        files={"file": ("layout.json", b"{ invalid }", "application/json")},
    )
    assert response.status_code == 400


# ── GET /shapely/example-layout ──────────────────────────────────────────

def test_example_layout_returns_200_or_404(client):
    """GET /shapely/example-layout returns 200 with JSON or 404 if file missing."""
    response = client.get("/shapely/example-layout")
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, (dict, list))
    else:
        assert response.status_code == 404


# ── POST /shapely/blueprint ──────────────────────────────────────────────

def test_blueprint_missing_body_returns_422(client):
    """POST /shapely/blueprint with no body may return 422."""
    response = client.post("/shapely/blueprint")
    assert response.status_code in (400, 422)


def test_blueprint_invalid_json_returns_400(client):
    """POST /shapely/blueprint with invalid JSON returns 400."""
    response = client.post(
        "/shapely/blueprint",
        content="not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
