"""
Pytest configuration and shared fixtures for GaaZoo API tests.
Ensures backend directory is on path and provides TestClient.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure backend is on path when running tests (e.g. from repo root or backend/tests)
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from main import app


@pytest.fixture
def client():
    """FastAPI TestClient for making requests without starting a server."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_session():
    """Client that preserves session cookies across requests (e.g. for auth/profile flows)."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
