"""Tests for health check API."""

import pytest


def test_health_returns_ok(client):
    """GET /health returns status ok and service name."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "GaaZoo" in data.get("service", "")
    assert data.get("framework") == "FastAPI"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
