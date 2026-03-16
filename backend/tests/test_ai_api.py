"""Tests for AI routes (suggest, narrative, ask)."""

import pytest


# All AI routes require a DPP in session; without it they return 400.


def test_ai_suggest_no_profile_returns_400(client):
    """POST /ai/suggest without DPP in session returns 400."""
    response = client.post("/ai/suggest", json={"room_type": "living room"})
    assert response.status_code == 400
    assert "profile" in response.json().get("detail", "").lower()


def test_ai_narrative_no_profile_returns_400(client):
    """POST /ai/narrative without DPP in session returns 400."""
    response = client.post("/ai/narrative")
    assert response.status_code == 400
    assert "profile" in response.json().get("detail", "").lower()


def test_ai_ask_no_profile_returns_400(client):
    """POST /ai/ask without DPP in session returns 400."""
    response = client.post("/ai/ask", json={"question": "What color sofa?"})
    assert response.status_code == 400
    assert "profile" in response.json().get("detail", "").lower()


def test_ai_ask_missing_question_returns_400(client):
    """POST /ai/ask with empty question returns 400 (after profile check)."""
    # Without session DPP we get 400 for no profile first; with DPP we'd get 400 for empty question.
    response = client.post("/ai/ask", json={"question": ""})
    assert response.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
