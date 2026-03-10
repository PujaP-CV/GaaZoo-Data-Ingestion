"""
routes/ai_routes.py
--------------------
Gemini AI design routes:
  POST /ai/suggest        — generate room design suggestions
  POST /ai/narrative      — generate human-readable profile narrative
  POST /ai/ask            — ask a free-form design question
"""

from flask import Blueprint, jsonify, request, session, current_app
from modules.gemini_ai import (
    generate_design_suggestions,
    generate_profile_narrative,
    answer_design_question,
)

ai_bp = Blueprint("ai", __name__)


def _require_profile(f):
    """Decorator — return 400 if no DPP has been built yet."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("dpp"):
            return jsonify({
                "error": "No design profile found. Please connect Pinterest and build your profile first."
            }), 400
        return f(*args, **kwargs)
    return decorated


@ai_bp.route("/suggest", methods=["POST"])
@_require_profile
def suggest():
    """
    Generate room design suggestions based on the user's DPP.

    Request body (JSON):
        { "room_type": "living room" }   ← optional, defaults to living room
    """
    dpp  = session["dpp"]
    body = request.get_json(silent=True) or {}
    room_type = body.get("room_type", "living room")

    try:
        result = generate_design_suggestions(dpp, room_type)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        current_app.logger.error(f"AI suggest failed: {e}")
        return jsonify({"error": str(e)}), 500


@ai_bp.route("/narrative", methods=["POST"])
@_require_profile
def narrative():
    """
    Generate a warm human-readable description of the user's design personality.
    """
    dpp = session["dpp"]

    try:
        text = generate_profile_narrative(dpp)
        return jsonify({"success": True, "narrative": text})
    except Exception as e:
        current_app.logger.error(f"AI narrative failed: {e}")
        return jsonify({"error": str(e)}), 500


@ai_bp.route("/ask", methods=["POST"])
@_require_profile
def ask():
    """
    Answer a free-form interior design question, personalised to the user's DPP.

    Request body (JSON):
        { "question": "What sofa should I get?" }
    """
    dpp  = session["dpp"]
    body = request.get_json(silent=True) or {}
    question = body.get("question", "").strip()

    if not question:
        return jsonify({"error": "Please provide a question."}), 400

    try:
        answer = answer_design_question(dpp, question)
        return jsonify({"success": True, "answer": answer})
    except Exception as e:
        current_app.logger.error(f"AI ask failed: {e}")
        return jsonify({"error": str(e)}), 500
