"""Tests for catalog and data-ingestion APIs."""

from pathlib import Path
from unittest.mock import patch

import pytest

# Path to a real image used in add-local-vendor tests so the API writes valid files (openable in Photos).
_TEST_IMAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "test_sofa.JPG"
# Minimal valid 1x1 pixel JPEG (fallback when test_sofa.JPG is missing, e.g. in CI).
_MINIMAL_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c \x24."
    b"\x27 \x22,\x23\x1c\x1c(7),\x30\x31444\x1f'9=82<\x2342"
    b"\xff\xdb\x00C\x01\t\t\t\x0c\x0b\x0c\x18\r\r\x182!\x1c!222222222222222222222222222222222222\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&'()*56789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xfb\x8c\x00\x0f\xff\xd9"
)


def _valid_jpeg_bytes():
    """Return valid JPEG bytes so saved files open in viewers (no corrupted image)."""
    if _TEST_IMAGE_PATH.exists():
        return _TEST_IMAGE_PATH.read_bytes()
    return _MINIMAL_JPEG


# ── GET /api/catalog ───────────────────────────────────────────────────

def test_catalog_list_returns_200(client):
    """GET /api/catalog returns 200 and items list (DB may be empty or unavailable)."""
    response = client.get("/api/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)


def test_catalog_list_accepts_status_limit_offset(client):
    """GET /api/catalog accepts query params status, limit, offset."""
    response = client.get("/api/catalog?status=all&limit=10&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


# ── GET /api/catalog/{asin} ─────────────────────────────────────────────

def test_catalog_item_not_found_returns_404(client):
    """GET /api/catalog/{asin} returns 404 for unknown asin."""
    response = client.get("/api/catalog/nonexistent_asin_12345")
    assert response.status_code == 404
    assert "not found" in response.json().get("detail", "").lower()


@patch("routes.catalog_routes.get_item_by_asin")
def test_catalog_item_returns_enriched_item(mock_get, client):
    """GET /api/catalog/{asin} returns enriched item when found."""
    mock_get.return_value = {
        "asin": "test_asin",
        "title": "Test Product",
        "image_url": "https://example.com/img.jpg",
        "image_path_used": None,
        "glb_path": None,
        "image_paths": None,
    }
    response = client.get("/api/catalog/test_asin")
    assert response.status_code == 200
    data = response.json()
    assert data["asin"] == "test_asin"
    assert data["title"] == "Test Product"
    assert "image_url" in data
    assert "image_url_original" in data


# ── DELETE /api/catalog/{asin} ──────────────────────────────────────────

@patch("modules.catalog_db.delete_item")
def test_catalog_delete_not_found_returns_404(mock_delete, client):
    """DELETE /api/catalog/{asin} returns 404 when item does not exist."""
    mock_delete.return_value = False
    response = client.delete("/api/catalog/nonexistent_asin")
    assert response.status_code == 404


@patch("modules.catalog_db.delete_item")
def test_catalog_delete_success_returns_ok(mock_delete, client):
    """DELETE /api/catalog/{asin} returns 200 with ok when deleted."""
    mock_delete.return_value = True
    response = client.delete("/api/catalog/some_asin")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "asin": "some_asin"}


# ── POST /api/fetch-images ──────────────────────────────────────────────

def test_fetch_images_missing_query_returns_400(client):
    """POST /api/fetch-images without query returns 400."""
    response = client.post("/api/fetch-images", json={"source": "amazon"})
    assert response.status_code == 400
    assert "query" in response.json().get("error", "").lower()


def test_fetch_images_invalid_source_returns_400(client):
    """POST /api/fetch-images with invalid source returns 400."""
    response = client.post(
        "/api/fetch-images",
        json={"source": "invalid", "query": "chair"},
    )
    assert response.status_code == 400
    assert "amazon" in response.json().get("error", "").lower() or "google" in response.json().get("error", "").lower()


@patch("config.RAPIDAPI_KEY", "test-key")
@patch("pipelines.pipeline_amazon.run_amazon_pipeline")
def test_fetch_images_amazon_success(mock_amazon, client):
    """POST /api/fetch-images with source=amazon returns 200 and items when pipeline returns data.
    Mocks the whole pipeline so no real API/Neo4j; nothing is stored in the database.
    """
    mock_amazon.return_value = [
        {"asin": "B001", "title": "Test Product", "image_url": "https://example.com/1.jpg"},
    ]
    response = client.post(
        "/api/fetch-images",
        json={"source": "amazon", "query": "red sofa", "country": "IN"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert data.get("count") == 1
    assert data.get("amazon_count") == 1
    assert len(data.get("items", [])) == 1
    assert data["items"][0].get("source") == "amazon"
    assert data["items"][0].get("asin") == "B001"
    mock_amazon.assert_called_once()


@patch("config.SERPAPI_KEY", "test-key")
@patch("pipelines.pipeline_serp.run_serp_pipeline")
def test_fetch_images_google_success(mock_serp, client):
    """POST /api/fetch-images with source=google returns 200 and items when pipeline returns data.
    Mocks the whole pipeline so no real API/Neo4j; nothing is stored in the database.
    """
    mock_serp.return_value = [
        {"title": "Chair from Google", "image_url": "https://example.com/chair.jpg"},
    ]
    response = client.post(
        "/api/fetch-images",
        json={"source": "google", "query": " office chair", "num_serp": 1},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert data.get("count") == 1
    assert data.get("serp_count") == 1
    assert len(data.get("items", [])) == 1
    assert data["items"][0].get("source") == "serp"
    mock_serp.assert_called_once()


@pytest.mark.integration
def test_fetch_images_amazon_real_saves_to_neo4j(client):
    """Fetch real data and images from Amazon API and save to Neo4j.
    Run with: pytest tests/test_catalog_api.py -m integration -v
    Requires: Neo4j running, RAPIDAPI_KEY set in .env. Uses real RapidAPI (1 product).
    """
    import config
    if not (getattr(config, "RAPIDAPI_KEY", "") or "").strip():
        pytest.skip("RAPIDAPI_KEY not set in .env — skip real Amazon fetch")
    response = client.post(
        "/api/fetch-images",
        json={
            "source": "amazon",
            "query": "Green sofa",
            "country": "IN",
            "max_amazon": 1,
            "max_images_per_product": 1,
        },
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data.get("ok") is True
    items = data.get("items", [])
    assert len(items) >= 1, "Expected at least one item from Amazon"
    asin = items[0].get("asin")
    assert asin
    get_response = client.get(f"/api/catalog/{asin}")
    assert get_response.status_code == 200, f"Item {asin} should be in Neo4j"
    stored = get_response.json()
    assert stored.get("asin") == asin
    assert stored.get("title")


@pytest.mark.integration
def test_fetch_images_google_real_saves_to_neo4j(client):
    """Fetch real data and images from Google (SerpAPI) and save to Neo4j.
    Run with: pytest tests/test_catalog_api.py -m integration -v
    Requires: Neo4j running, SERPAPI_KEY set in .env. Uses real SerpAPI (2 results).
    """
    import config
    if not (getattr(config, "SERPAPI_KEY", "") or "").strip():
        pytest.skip("SERPAPI_KEY not set in .env — skip real Google fetch")
    response = client.post(
        "/api/fetch-images",
        json={
            "source": "google",
            "query": "office chair",
            "num_serp": 2,
        },
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data.get("ok") is True
    items = data.get("items", [])
    assert len(items) >= 1, "Expected at least one item from Google"
    asin = items[0].get("asin")
    assert asin
    get_response = client.get(f"/api/catalog/{asin}")
    assert get_response.status_code == 200, f"Item {asin} should be in Neo4j"
    stored = get_response.json()
    assert stored.get("asin") == asin
    assert stored.get("title")


# ── POST /api/add-local-vendor ──────────────────────────────────────────

def test_add_local_vendor_missing_title_returns_400(client):
    """POST /api/add-local-vendor without title returns 400 or 422 (validation)."""
    response = client.post(
        "/api/add-local-vendor",
        data={"title": ""},
        files={"image": ("test_sofa.JPG", _valid_jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code in (400, 422)
    body = response.json()
    assert "title" in body.get("error", "") or "detail" in body


def test_add_local_vendor_no_image_returns_400(client):
    """POST /api/add-local-vendor without image file returns 400."""
    response = client.post(
        "/api/add-local-vendor",
        data={"title": "Test Product"},
        files={},  # no image
    )
    assert response.status_code == 422  # FastAPI validation error for missing File(...)


@patch("routes.catalog_routes.upsert_item")
def test_add_local_vendor_success_returns_asin(mock_upsert, client):
    """POST /api/add-local-vendor with valid image and title returns 200 and asin.
    Mocks upsert_item so no Neo4j is required; image is saved to disk but not to DB.
    """
    mock_upsert.return_value = None
    response = client.post(
        "/api/add-local-vendor",
        data={
            "title": "Test Sofa",
            "vendor_name": "Local vendor",
            "product_type": "Furniture",
            "product_subtype": "Sofa",
            "colour": "Brown",
            "style": "Modern",
            "material": "Leather",
            "source_url": "https://www.amazon.com/dp/B08N5WRWNW",
            "product_dimensions": "80 x 40 x 30 inches",
            "width": 80,
            "height": 40
        },
        files={"image": ("test_sofa.JPG", _valid_jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert "asin" in data
    assert data["asin"].startswith("local_")
    assert "item" in data


@pytest.mark.integration
def test_add_local_vendor_saves_to_neo4j(client):
    """POST /api/add-local-vendor and actually save to Neo4j (no mock).
    Run with: pytest tests/test_catalog_api.py -m integration -v
    Requires Neo4j running and NEO4J_URI/NEO4J_PASSWORD in .env.
    """
    response = client.post(
        "/api/add-local-vendor",
        data={
            "title": "Test Sofa (integration)",
            "vendor_name": "Local vendor",
            "product_type": "Furniture",
            "product_subtype": "Sofa",
            "colour": "Brown",
            "style": "Modern",
            "material": "Leather",
            "source_url": "https://www.amazon.com/dp/B08N5WRWNW",
            "product_dimensions": "80 x 40 x 30 inches",
            "width": 80,
            "height": 40,
        },
        files={"image": ("test_sofa.JPG", _valid_jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    asin = data.get("asin")
    assert asin and asin.startswith("local_")
    # Verify it's in the DB: GET /api/catalog/{asin} should return the item
    get_response = client.get(f"/api/catalog/{asin}")
    assert get_response.status_code == 200
    assert get_response.json().get("asin") == asin
    assert get_response.json().get("title") == "Test Sofa (integration)"


# ── POST /api/convert-selected, POST /api/convert-item ──────────────────

@patch("pipelines.pipeline_3d.run_3d_pipeline")
def test_convert_selected_success(mock_run, client):
    """POST /api/convert-selected returns results from pipeline."""
    mock_run.return_value = [{"asin": "a1", "status": "ok"}]
    response = client.post("/api/convert-selected", json={"limit": 2})
    assert response.status_code == 200
    assert response.json().get("ok") is True
    assert "results" in response.json()


def test_convert_item_missing_asin_returns_400(client):
    """POST /api/convert-item without asin returns 400."""
    response = client.post("/api/convert-item", json={})
    assert response.status_code == 400
    assert "asin" in response.json().get("error", "").lower()


@patch("routes.catalog_routes.get_item_by_asin")
def test_convert_item_not_found_returns_404(mock_get, client):
    """POST /api/convert-item for unknown asin returns 404."""
    mock_get.return_value = None
    response = client.post("/api/convert-item", json={"asin": "unknown"})
    assert response.status_code == 404
    assert "not found" in response.json().get("error", "").lower()


# ── GET /api/files/{subpath} ─────────────────────────────────────────────

def test_files_invalid_path_returns_400(client):
    """GET /api/files with invalid path returns 400 or 404 (path may be normalized)."""
    response = client.get("/api/files/../etc/passwd")
    # 400 if route rejects ".."; 404 if URL is normalized before route sees it
    assert response.status_code in (400, 404)
    response2 = client.get("/api/files//etc/passwd")
    assert response2.status_code in (400, 404)


# ── Dollhouse APIs ───────────────────────────────────────────────────────

@patch("routes.catalog_routes.list_dollhouses")
def test_dollhouse_list_returns_200(mock_list, client):
    """GET /api/dollhouse returns 200 and items list."""
    mock_list.return_value = []
    response = client.get("/api/dollhouse")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)


def test_dollhouse_get_not_found_returns_404(client):
    """GET /api/dollhouse/{id} returns 404 for unknown id."""
    response = client.get("/api/dollhouse/dh_nonexistent123")
    assert response.status_code == 404
    assert "dollhouse" in response.json().get("detail", "").lower()


def test_dollhouse_create_missing_name_returns_400(client):
    """POST /api/dollhouse without name returns 400 or 422 (validation)."""
    response = client.post(
        "/api/dollhouse",
        data={"name": "", "scan_json": "{}"},
        files={"usdz_file": ("model.usdz", b"PK\x03\x04", "application/octet-stream")},
    )
    assert response.status_code in (400, 422)
    body = response.json()
    assert "name" in body.get("error", "") or "detail" in body


def test_dollhouse_create_non_usdz_returns_400(client):
    """POST /api/dollhouse with non-.usdz file returns 400."""
    response = client.post(
        "/api/dollhouse",
        data={"name": "Test", "scan_json": "{}"},
        files={"usdz_file": ("model.glb", b"glb", "model/gltf-binary")},
    )
    assert response.status_code == 400
    assert "usdz" in response.json().get("error", "").lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
