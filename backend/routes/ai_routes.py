"""
routes/ai_routes.py
--------------------
Gemini AI design routes:
  POST /ai/suggest    — generate room design suggestions
  POST /ai/narrative  — generate human-readable profile narrative
  POST /ai/ask        — ask a free-form design question
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from modules.gemini_ai import (
    generate_design_suggestions,
    generate_profile_narrative,
    answer_design_question,
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def _require_profile(request: Request) -> dict:
    """Return the stored DPP or raise 400 if not built yet."""
    dpp = request.session.get("dpp")
    if not dpp:
        raise HTTPException(
            status_code=400,
            detail="No design profile found. Please connect Pinterest / Spotify and build your profile first.",
        )
    return dpp


@router.post("/suggest")
async def suggest(request: Request):
    """Generate room design suggestions based on the user's DPP."""
    dpp  = await _require_profile(request)
    body = await request.json()
    room_type = body.get("room_type", "living room")

    try:
        result = generate_design_suggestions(dpp, room_type)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"AI suggest failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/narrative")
async def narrative(request: Request):
    """Generate a warm human-readable description of the user's design personality."""
    dpp = await _require_profile(request)

    try:
        text = generate_profile_narrative(dpp)
        return {"success": True, "narrative": text}
    except Exception as e:
        logger.error(f"AI narrative failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ask")
async def ask(request: Request):
    """Answer a free-form interior design question personalised to the user's DPP."""
    dpp  = await _require_profile(request)
    body = await request.json()
    question = body.get("question", "").strip()

    if not question:
        raise HTTPException(status_code=400, detail="Please provide a question.")

    try:
        answer = answer_design_question(dpp, question)
        return {"success": True, "answer": answer}
    except Exception as e:
        logger.error(f"AI ask failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
