"""
modules/gemini_ai.py
---------------------
All AI interactions for GaaZoo.

Previously called Gemini directly. Now calls the ProcessIQ Vanilla Prompt API
which fetches prompts from the Excel sheet and runs them through the chosen LLM.

Vanilla API endpoint:
  POST https://ac360.conceptvines.com/process-iq/template/vanilla_prompt_api

Multipart fields:
  template_id     — int, matches Template No column in Excel
  template_name   — string label (informational)
  parameters      — JSON string of {placeholder: value} substitutions
  llm             — "openai" | "gemini" | "claude"
  document_files  — (optional) image/file attachments

GaaZoo Template IDs in Excel:
  15 — GaaZoo Image Analyser      → vision analysis of one room image
  16 — GaaZoo DPP Enrichment      → raw signals → full structured DPP JSON + narrative
  17 — GaaZoo Design Suggestions  → DPP → 3 purchasable room suggestions
  18 — GaaZoo Design Q&A          → DPP + question → personalised answer
  19 — GaaZoo Image Q Multi       → per-image fixed question + 4 AI-generated options
  20 — GaaZoo Image Q Dimension   → per-image AI-picked dimension + focused question
  21 — GaaZoo DNA Predictor       → aggregated signals → 12 material/shape slider scores
"""

import base64
import io
import json
import logging
import re

import requests

from config import Config

logger = logging.getLogger(__name__)

# ── Vanilla API core ──────────────────────────────────────────────────────────

VANILLA_API_URL = (
    "https://ac360.conceptvines.com/process-iq/template/vanilla_prompt_api"
)


def _call_vanilla(
    template_id: int,
    template_name: str,
    parameters: dict,
    image_bytes_list: list = None,  # [(filename, bytes, mimetype), ...]
    llm: str = "openai",
) -> str:
    """
    POST to vanilla prompt API and return the LLM response as a string.

    The API reads the prompt from Excel by template_id, substitutes
    {placeholder} values from parameters, and returns the LLM result.
    """
    data = {
        "template_id": str(template_id),
        "template_name": template_name,
        "parameters": json.dumps(parameters),
        "llm": llm,
    }

    files = []
    for filename, img_bytes, mimetype in image_bytes_list or []:
        files.append(("document_files", (filename, io.BytesIO(img_bytes), mimetype)))

    logger.info(
        f"Vanilla API → template_id={template_id} | llm={llm} | "
        f"images={len(image_bytes_list or [])} | params={list(parameters.keys())}"
    )

    try:
        # Debug: log full outgoing payload (parameters are JSON string)
        try:
            logger.debug(
                f"Vanilla API request data: {json.dumps(data, ensure_ascii=False)}"
            )
        except Exception:
            logger.debug(f"Vanilla API request data (raw): {data}")

        resp = requests.post(
            VANILLA_API_URL,
            data=data,
            files=files if files else None,
            timeout=120,
        )
        logger.debug(f"Vanilla API response status: {resp.status_code}")
    except requests.exceptions.Timeout:
        raise Exception("Vanilla API timed out (120s)")
    except requests.exceptions.ConnectionError:
        raise Exception("Cannot reach Vanilla API — check network/VPN")

    if not resp.ok:
        # Log full error body for diagnostics
        logger.error(f"Vanilla API error {resp.status_code}: {resp.text}")
        raise Exception(f"Vanilla API {resp.status_code}: {resp.text[:300]}")

    # Parse response — API returns JSON, extract the LLM text
    try:
        # Log full response text (useful when API returns nested JSON or lists)
        try:
            logger.debug(f"Vanilla API response text: {resp.text}")
        except Exception:
            logger.debug("Vanilla API response text (unprintable)")

        body = resp.json()
        try:
            logger.debug(
                f"Vanilla API response keys: {list(body.keys()) if isinstance(body, dict) else type(body)}"
            )
            logger.debug(
                f"Vanilla API response body: {json.dumps(body, ensure_ascii=False)}"
            )
        except Exception:
            logger.debug(f"Vanilla API response body (raw): {body}")
        # Try common response keys in order
        for key in (
            "result",
            "response",
            "content",
            "text",
            "output",
            "answer",
            "data",
        ):
            if key in body and body[key]:
                val = body[key]
                # Handle nested: {"result": {"content": "..."}}
                if isinstance(val, dict):
                    for inner in ("content", "text", "message", "result", "response"):
                        if inner in val:
                            inner_val = val[inner]
                            # Return raw string if already a string, otherwise JSON encode
                            if isinstance(inner_val, str):
                                return inner_val
                            return json.dumps(inner_val, ensure_ascii=False)
                # If it's a list or other structured type, serialize to JSON
                if isinstance(val, (dict, list)):
                    return json.dumps(val, ensure_ascii=False)
                return str(val)
        # Fallback: return full JSON string
        return json.dumps(body, ensure_ascii=False)
    except Exception:
        return resp.text  # Not JSON — return raw


def _parse_json_response(raw: str) -> dict:
    """Strip markdown fences and parse JSON from LLM text response."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```\s*$", "", raw.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"No JSON in response: {raw[:200]}")


# ── Template 15: Image Analysis ───────────────────────────────────────────────


def analyse_single_image_vanilla(
    filename: str, img_bytes: bytes, mimetype: str
) -> dict:
    """
    Send one room image to Template 15 (GaaZoo Image Analyser).
    Returns structured vision dict: styles, colours, materials, mood_tags etc.

    Equivalent curl:
      curl -X POST .../vanilla_prompt_api
        -F template_id=15
        -F template_name="GaaZoo Image Analyser"
        -F parameters={}
        -F llm=openai
        -F document_files=@room.jpg
    """
    try:
        raw = _call_vanilla(
            template_id=15,
            template_name="GaaZoo Image Analyser",
            parameters={},
            image_bytes_list=[(filename, img_bytes, mimetype)],
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        logger.debug(f"Vanilla API raw response (truncated): {raw[:200]}")
        data = _parse_json_response(raw)
        # Normalise: API sometimes returns a list [ {llm, executed_prompt, response: "..."} ]
        # and sometimes returns a dict with structured keys. Handle both.
        if isinstance(data, list):
            first = data[0] if len(data) else {}
            if isinstance(first, dict):
                # If the LLM response is embedded as a string under 'response', try to parse it as JSON
                inner_text = first.get("response") or first.get("result") or None
                if isinstance(inner_text, str):
                    try:
                        inner = _parse_json_response(inner_text)
                        if isinstance(inner, dict):
                            data = inner
                        else:
                            data = {"analysis_text": inner_text}
                    except Exception:
                        data = {"analysis_text": inner_text}
                else:
                    # first may already contain structured keys
                    data = first
            else:
                # list of strings or other — capture as text
                data = {"analysis_text": " ".join(str(x) for x in data)}
        if not isinstance(data, dict):
            data = {"analysis_text": str(data)}
        # dominant_colours may be [{hex, label}] objects or plain hex strings —
        # normalise to [{hex, label}] so downstream code has a consistent shape.
        raw_colours = data.get("dominant_colours", [])
        normalised_colours = []
        for c in raw_colours:
            if isinstance(c, dict) and c.get("hex"):
                normalised_colours.append(
                    {"hex": c["hex"], "label": c.get("label", "")}
                )
            elif isinstance(c, str) and c:
                normalised_colours.append({"hex": c, "label": ""})

        return {
            "filename": filename,
            "styles": data.get("styles", []),
            "dominant_colours": normalised_colours,
            "materials": data.get("materials", []),
            "mood_tags": data.get("mood_tags", []),
            "spatial_density": data.get("spatial_density", "moderate"),
            "confidence": float(
                data.get("confidence", 0.7)
                if data.get("confidence") is not None
                else 0.7
            ),
            "analysis_text": data.get("analysis_text")
            or data.get("response")
            or data.get("result")
            or None,
        }
    except Exception as e:
        logger.warning(f"Image analysis failed [{filename}]: {e}")
        return {
            "filename": filename,
            "error": str(e),
            "styles": [],
            "dominant_colours": [],
            "materials": [],
            "mood_tags": [],
            "spatial_density": "unknown",
            "confidence": 0.0,
        }


def analyse_single_image_with_questions(
    filename: str, img_bytes: bytes, mimetype: str, template_id: int
) -> dict:
    """
    Send one room image to Template 19 or 20 (configured via IMAGE_QUESTION_TEMPLATE_ID).
    Returns the full analysis dict PLUS 'question' and 'options' fields for the UI.

    Template 19 — Option B: fixed question, 4 AI-generated options based on image.
    Template 20 — Option C: AI picks design dimension, writes focused question + 4 options.

    Falls back to analyse_single_image_vanilla (Template 15) if the question
    template fails, so the flow never breaks — questions just won't appear.
    """
    template_name = (
        "GaaZoo Image Q - Multi-select"
        if template_id == 19
        else "GaaZoo Image Q - Dimension Focus"
    )
    try:
        raw = _call_vanilla(
            template_id=template_id,
            template_name=template_name,
            parameters={},
            image_bytes_list=[(filename, img_bytes, mimetype)],
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        data = _parse_json_response(raw)

        # Unwrap list wrapper if needed (same logic as analyse_single_image_vanilla)
        if isinstance(data, list):
            first = data[0] if data else {}
            if isinstance(first, dict):
                inner = first.get("response") or first.get("result")
                if isinstance(inner, str):
                    try:
                        data = _parse_json_response(inner)
                    except Exception:
                        data = {"analysis_text": inner}
                else:
                    data = first
            else:
                data = {"analysis_text": " ".join(str(x) for x in data)}
        if not isinstance(data, dict):
            data = {"analysis_text": str(data)}

        # Normalise colours — same as analyse_single_image_vanilla
        raw_colours = data.get("dominant_colours", [])
        normalised_colours = []
        for c in raw_colours:
            if isinstance(c, dict) and c.get("hex"):
                normalised_colours.append(
                    {"hex": c["hex"], "label": c.get("label", "")}
                )
            elif isinstance(c, str) and c:
                normalised_colours.append({"hex": c, "label": ""})

        return {
            "filename": filename,
            "styles": data.get("styles", []),
            "dominant_colours": normalised_colours,
            "materials": data.get("materials", []),
            "mood_tags": data.get("mood_tags", []),
            "spatial_density": data.get("spatial_density", "moderate"),
            "confidence": float(data.get("confidence", 0.7) or 0.7),
            # Question fields — present only in T19/T20 responses
            "question": data.get("question", "What drew you to this image?"),
            "options": data.get("options", []),
            # T20 only — which design dimension the AI focused on
            "dimension": data.get("dimension"),
        }

    except Exception as e:
        logger.warning(
            f"Image question analysis failed [{filename}] (template {template_id}): {e}"
            " — falling back to Template 15"
        )
        # Graceful fallback: return T15 result with empty question/options
        result = analyse_single_image_vanilla(filename, img_bytes, mimetype)
        result.setdefault("question", "What drew you to this image?")
        result.setdefault("options", [])
        result.setdefault("dimension", None)
        return result


# ── Template 21: Material/Shape DNA Predictor ────────────────────────────────

# 12 slider keys — used as fallback neutral values
_DNA_DEFAULTS = {
    "material_dna": {
        "natural_industrial": 0.5,
        "matte_glossy": 0.5,
        "warm_cool": 0.5,
        "soft_hard": 0.5,
        "minimal_layered": 0.5,
        "rustic_refined": 0.5,
    },
    "shape_dna": {
        "light_dark_wood": 0.5,
        "smooth_textured": 0.5,
        "low_high_contrast": 0.5,
        "precision_handcrafted": 0.5,
        "uniform_patterned": 0.5,
        "flat_deep_finish": 0.5,
    },
}


def predict_material_shape_dna(aggregated_signals: dict) -> dict:
    """
    Call Template 21 (GaaZoo DNA Predictor) with aggregated image signals.

    Input: aggregated signal dict with keys:
        styles, materials, colours, mood_tags, spatial_density

    Returns:
        {
          "material_dna": {natural_industrial, matte_glossy, warm_cool,
                           soft_hard, minimal_layered, rustic_refined},
          "shape_dna":    {light_dark_wood, smooth_textured, low_high_contrast,
                           precision_handcrafted, uniform_patterned, flat_deep_finish}
        }
    All values are floats 0.0–1.0.  Graceful fallback to 0.5 on any error.
    """
    import copy

    defaults = copy.deepcopy(_DNA_DEFAULTS)

    # Build compact signal text for the prompt
    styles = ", ".join(aggregated_signals.get("styles", [])[:6]) or "unknown"
    materials = ", ".join(aggregated_signals.get("materials", [])[:8]) or "unknown"
    mood_tags = ", ".join(aggregated_signals.get("mood_tags", [])[:6]) or "unknown"
    spatial = aggregated_signals.get("spatial_density", "moderate")
    raw_colours = aggregated_signals.get("colours", [])
    colour_strs = []
    for c in raw_colours[:5]:
        if isinstance(c, dict) and c.get("hex"):
            colour_strs.append(f"{c['hex']} {c.get('label', '')}".strip())
        elif isinstance(c, str):
            colour_strs.append(c)
    colours_text = ", ".join(colour_strs) or "unknown"

    params = {
        "styles": styles,
        "materials": materials,
        "colours": colours_text,
        "mood_tags": mood_tags,
        "spatial_density": spatial,
    }
    print("dna params.......................\n", params)

    try:
        raw = _call_vanilla(
            template_id=21,
            template_name="GaaZoo DNA Predictor",
            parameters=params,
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        data = _parse_json_response(raw)
        print("raw dna response.......................\n", data)
        # Unwrap list wrapper if needed
        if isinstance(data, list):
            first = data[0] if data else {}
            if isinstance(first, dict):
                inner = first.get("response") or first.get("result")
                if isinstance(inner, str):
                    try:
                        data = _parse_json_response(inner)
                    except Exception:
                        data = {}
                else:
                    data = first

        if not isinstance(data, dict):
            raise ValueError(f"Unexpected DNA response type: {type(data)}")

        def _clamp(v):
            try:
                return max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                return 0.5

        # Extract and clamp material_dna
        md_raw = data.get("material_dna", {})
        sd_raw = data.get("shape_dna", {})

        material_dna = {k: _clamp(md_raw.get(k, 0.5)) for k in defaults["material_dna"]}
        shape_dna = {k: _clamp(sd_raw.get(k, 0.5)) for k in defaults["shape_dna"]}

        result = {"material_dna": material_dna, "shape_dna": shape_dna}
        logger.info("DNA prediction succeeded via Template 21")
        return result

    except Exception as e:
        logger.warning(
            f"DNA prediction failed (Template 21): {e} — using neutral 0.5 defaults"
        )
        return defaults


# ── Template 24: Spotify features → Mood vector ────────────────────────────────


def spotify_mood_vector(
    top_artists: str,
    top_tracks: str,
    top_genres: str,
    audio_features: str,
) -> dict:
    """
    Call Template 24 to convert Spotify data into a mood vector.
    Returns dict with calm_energetic, warm_edgy, minimal_maximal, vintage_modern (0.0–1.0).
    """
    params = {
        "top_artists": top_artists or "unknown",
        "top_tracks": top_tracks or "unknown",
        "top_genres": top_genres or "unknown",
        "audio_features": audio_features if audio_features else "not provided",
    }
    try:
        raw = _call_vanilla(
            template_id=24,
            template_name="GaaZoo Spotify → Mood Vector",
            parameters=params,
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        data = _parse_json_response(raw)
        if isinstance(data, list):
            first = data[0] if data else {}
            if isinstance(first, dict):
                inner = first.get("response") or first.get("result")
                if isinstance(inner, str):
                    data = _parse_json_response(inner)
                else:
                    data = first
            else:
                data = {}
        if not isinstance(data, dict):
            data = {}
        # Ensure four keys with floats 0–1
        def _clamp(v):
            try:
                return max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                return 0.5
        keys = ("calm_energetic", "warm_edgy", "minimal_maximal", "vintage_modern")
        return {k: _clamp(data.get(k, 0.5)) for k in keys}
    except Exception as e:
        logger.warning(f"Spotify mood vector (Template 24) failed: {e}")
        return {k: 0.5 for k in ("calm_energetic", "warm_edgy", "minimal_maximal", "vintage_modern")}


# ── Template 25: Mood vector → Interior attributes ──────────────────────────────


def spotify_mood_to_attributes(mood_vector: dict) -> dict:
    """
    Call Template 25 to map mood vector to interior design attributes.
    Returns dict with styles, materials, colours, mood_tags, spatial_density, confidence.
    Same shape as spotify_design_signals for downstream DNA and build.
    """
    import json as _json
    mood_str = _json.dumps(mood_vector) if isinstance(mood_vector, dict) else str(mood_vector)
    params = {"mood_vector": mood_str}
    try:
        raw = _call_vanilla(
            template_id=25,
            template_name="GaaZoo Mood Vector → Interior Attributes",
            parameters=params,
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        data = _parse_json_response(raw)
        if isinstance(data, list):
            first = data[0] if data else {}
            if isinstance(first, dict):
                inner = first.get("response") or first.get("result")
                if isinstance(inner, str):
                    data = _parse_json_response(inner)
                else:
                    data = first
            else:
                data = {}
        if not isinstance(data, dict):
            data = {}
        # Normalise colours to [{hex, label}]
        raw_colours = data.get("colours", [])
        normalised = []
        for c in raw_colours:
            if isinstance(c, dict) and c.get("hex"):
                normalised.append({"hex": c["hex"], "label": c.get("label", "")})
            elif isinstance(c, str) and c:
                normalised.append({"hex": c, "label": ""})
        data["colours"] = normalised
        data.setdefault("dominant_colours", normalised)
        return data
    except Exception as e:
        logger.warning(f"Spotify mood→attributes (Template 25) failed: {e}")
        return {
            "styles": ["Contemporary"],
            "materials": ["linen", "wood"],
            "colours": [{"hex": "#C8B49A", "label": "warm sand"}],
            "mood_tags": ["calm", "warm"],
            "spatial_density": "moderate",
            "confidence": 0.5,
        }


# ── Template 26: Spotify question from signals ─────────────────────────────────


def spotify_question_from_signals(styles: list, mood_tags: list, playlist_names: str) -> dict:
    """
    Call Template 26 to generate a question + 2–4 options for the Spotify flow.
    Inputs are the interior styles/mood_tags derived from the user's playlists and
    the playlist_names string (for context in the question).

    Expected JSON from Template 26:
      {
        "question": "…",
        "options": ["…", "…", "…", "…"]
      }
    """
    # Debug: show what we're sending to Template 26
    print("Template 26 — spotify_question_from_signals input:",
          {"styles": styles, "mood_tags": mood_tags, "playlist_names": playlist_names})

    styles_text = ", ".join(styles[:6]) if styles else ""
    moods_text = ", ".join(mood_tags[:6]) if mood_tags else ""
    params = {
        "styles": styles_text or "unknown",
        "mood_tags": moods_text or "unknown",
        "playlist_names": playlist_names or "your playlists",
    }
    try:
        raw = _call_vanilla(
            template_id=26,
            template_name="GaaZoo Spotify Question from Signals",
            parameters=params,
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        print("Template 26 — raw response:", raw[:300] if isinstance(raw, str) else raw)
        data = _parse_json_response(raw)
        if isinstance(data, list):
            first = data[0] if data else {}
            if isinstance(first, dict):
                inner = first.get("response") or first.get("result")
                if isinstance(inner, str):
                    data = _parse_json_response(inner)
                else:
                    data = first
            else:
                data = {}
        if not isinstance(data, dict):
            raise ValueError("Unexpected response type")
        q = str(data.get("question") or "").strip()
        opts = data.get("options") or []
        print("Template 26 — parsed question/options:", q, opts)
        opts = [str(o).strip() for o in opts if str(o).strip()]
        return {"question": q, "options": opts[:4]}
    except Exception as e:
        logger.warning(f"Spotify question (Template 26) failed: {e}")
        print("Template 26 — ERROR:", e)
        return {"question": "", "options": []}


# ── Template 22: Spotify → Design Signals (legacy; prefer 24→25) ──────────────────


def spotify_design_signals(playlist_names: str, tracks_summary: str) -> dict:
    """
    Call Template 22 to infer design signals from Spotify playlist/track list.
    Returns dict with styles, materials, colours, mood_tags, spatial_density, confidence.
    """
    params = {
        "playlist_names": playlist_names or "My playlists",
        "tracks_summary": tracks_summary or "No tracks",
    }
    try:
        raw = _call_vanilla(
            template_id=22,
            template_name="GaaZoo Spotify Design Signals",
            parameters=params,
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        data = _parse_json_response(raw)
        if isinstance(data, list):
            first = data[0] if data else {}
            if isinstance(first, dict):
                inner = first.get("response") or first.get("result")
                if isinstance(inner, str):
                    data = _parse_json_response(inner)
                else:
                    data = first
            else:
                data = {}
        if not isinstance(data, dict):
            data = {}
        # Normalise colours to [{hex, label}]
        raw_colours = data.get("colours", [])
        normalised = []
        for c in raw_colours:
            if isinstance(c, dict) and c.get("hex"):
                normalised.append({"hex": c["hex"], "label": c.get("label", "")})
            elif isinstance(c, str) and c:
                normalised.append({"hex": c, "label": ""})
        data["colours"] = normalised
        data.setdefault("dominant_colours", normalised)
        return data
    except Exception as e:
        logger.warning(f"Spotify design signals (Template 22) failed: {e}")
        return {
            "styles": ["Contemporary"],
            "materials": ["linen", "wood"],
            "colours": [{"hex": "#C8B49A", "label": "warm sand"}],
            "mood_tags": ["calm", "warm"],
            "spatial_density": "moderate",
            "confidence": 0.5,
        }


# ── Template 23: Spotify DNA Predictor ────────────────────────────────────────
# Template 23 returns material_dna (6) + shape_dna (6) with these keys (update prompt to match):

SPOTIFY_MATERIAL_DNA_KEYS = [
    "energy_calm_energetic",   # Calm ↔ Energetic
    "mood_sad_happy",          # Sad ↔ Happy
    "acoustic_electronic",     # Acoustic ↔ Electronic
    "retro_modern",            # Retro ↔ Modern
    "instrumental_vocal",      # Instrumental ↔ Vocal
    "indie_mainstream",        # Indie ↔ Mainstream
]
SPOTIFY_SHAPE_DNA_KEYS = [
    "light_heavy",             # Light ↔ Heavy
    "smooth_raw",              # Smooth ↔ Raw
    "balanced_dynamic",        # Balanced ↔ Dynamic
    "polished_organic",       # Polished ↔ Organic
    "simple_complex",         # Simple ↔ Complex
    "minimal_layered",        # Minimal ↔ Layered
]


def predict_material_shape_dna_spotify(aggregated_signals: dict) -> dict:
    """
    Uses Template 23 (Spotify DNA Predictor). Expects Template 23 to return
    material_dna and shape_dna with SPOTIFY_MATERIAL_DNA_KEYS and SPOTIFY_SHAPE_DNA_KEYS.
    aggregated_signals: styles, materials, colours, mood_tags, spatial_density.
    """
    styles = ", ".join(aggregated_signals.get("styles", [])[:6]) or "unknown"
    materials = ", ".join(aggregated_signals.get("materials", [])[:8]) or "unknown"
    mood_tags = ", ".join(aggregated_signals.get("mood_tags", [])[:6]) or "unknown"
    spatial = aggregated_signals.get("spatial_density", "moderate")
    raw_colours = aggregated_signals.get("colours", [])
    colour_strs = []
    for c in raw_colours[:5]:
        if isinstance(c, dict) and c.get("hex"):
            colour_strs.append(f"{c['hex']} {c.get('label', '')}".strip())
        elif isinstance(c, str):
            colour_strs.append(c)
    colours_text = ", ".join(colour_strs) or "unknown"

    params = {
        "styles": styles,
        "materials": materials,
        "colours": colours_text,
        "mood_tags": mood_tags,
        "spatial_density": spatial,
    }
    defaults = {
        "material_dna": {k: 0.5 for k in SPOTIFY_MATERIAL_DNA_KEYS},
        "shape_dna": {k: 0.5 for k in SPOTIFY_SHAPE_DNA_KEYS},
    }

    try:
        raw = _call_vanilla(
            template_id=23,
            template_name="GaaZoo Spotify DNA Predictor",
            parameters=params,
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        data = _parse_json_response(raw)
        if isinstance(data, list):
            first = data[0] if data else {}
            if isinstance(first, dict):
                inner = first.get("response") or first.get("result")
                if isinstance(inner, str):
                    data = _parse_json_response(inner)
                else:
                    data = first
        if not isinstance(data, dict):
            raise ValueError("Unexpected response type")

        def _clamp(v):
            try:
                return max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                return 0.5

        md_raw = data.get("material_dna", {})
        sd_raw = data.get("shape_dna", {})
        material_dna = {k: _clamp(md_raw.get(k, 0.5)) for k in SPOTIFY_MATERIAL_DNA_KEYS}
        shape_dna = {k: _clamp(sd_raw.get(k, 0.5)) for k in SPOTIFY_SHAPE_DNA_KEYS}
        return {"material_dna": material_dna, "shape_dna": shape_dna}
    except Exception as e:
        logger.warning(
            f"Spotify DNA prediction (Template 23) failed: {e} — using defaults"
        )
        return defaults


# ── Template 16: DPP Enrichment ───────────────────────────────────────────────


def enrich_dpp_with_ai(raw_dpp: dict) -> dict:
    """
    Send raw DPP signals to Template 16 (GaaZoo DPP Enrichment).
    Gemini/OpenAI returns full structured DPP JSON with all 6 dimensions + narrative.

    Equivalent curl:
      curl -X POST .../vanilla_prompt_api
        -F template_id=16
        -F template_name="GaaZoo DPP Enrichment"
        -F 'parameters={"primary_style":"Minimalist","primary_weight":"55",...}'
        -F llm=openai
    """
    print("raw_dpp.......................\n", raw_dpp)
    params = _build_enrichment_params(raw_dpp)

    try:
        # Debug: log full enrichment parameters before calling Template 16
        try:
            logger.debug(
                f"Template 16 — enrichment parameters: {json.dumps(params, ensure_ascii=False)}"
            )
        except Exception:
            logger.debug(
                f"Template 16 — enrichment parameters (raw): {params}"
            )

        # Attempt to collect up to 4 example images from board_summary to send
        # with the enrichment request so the LLM can inspect colours/materials.
        image_bytes_list = []
        try:
            boards = raw_dpp.get("board_summary", []) or []
            max_images = 4
            for b in boards[:max_images]:
                url = b.get("image_url")
                if not url:
                    continue

                # Case A: data URL (uploaded image preview) -> decode and attach
                try:
                    s = str(url)
                    if s.startswith("data:"):
                        try:
                            header, b64 = s.split(",", 1)
                            m = re.match(r"data:(?P<type>[^;]+)(;base64)?", header)
                            mtype = (
                                m.group("type")
                                if m and m.group("type")
                                else "image/jpeg"
                            )
                            content = base64.b64decode(b64)
                            # Skip very large images
                            if len(content) > 2 * 1024 * 1024:
                                logger.debug(
                                    f"Skipping large data-url image {b.get('name')} ({len(content)} bytes)"
                                )
                                continue
                            fname = b.get("name") or "upload.jpg"
                            image_bytes_list.append((fname, content, mtype))
                            continue
                        except Exception as e:
                            logger.debug(
                                f"Failed to decode data-url image: {e}"
                            )
                            continue

                    # Case B: remote HTTP(S) URL -> download and attach
                    if s.lower().startswith("http"):
                        try:
                            r = requests.get(s, timeout=10)
                            if not r.ok:
                                continue
                            content = r.content
                            # Skip very large images
                            if len(content) > 2 * 1024 * 1024:
                                logger.debug(
                                    f"Skipping large board image {s} ({len(content)} bytes)"
                                )
                                continue
                            # Derive filename and mimetype
                            fname = (
                                s.split("?")[0].rstrip("/").split("/")[-1] or "img.jpg"
                            )
                            mtype = r.headers.get("content-type", "image/jpeg")
                            image_bytes_list.append((fname, content, mtype))
                        except Exception as e:
                            logger.debug(
                                f"Failed to download board image {s}: {e}"
                            )
                            continue
                    # Otherwise: skip non-http/non-data urls
                except Exception:
                    continue
        except Exception:
            image_bytes_list = []
        print("params.......................\n", params)
        raw = _call_vanilla(
            template_id=16,
            template_name="GaaZoo DPP Enrichment",
            parameters=params,
            # image_bytes_list=image_bytes_list if image_bytes_list else None,
            llm=Config().get("VANILLA_LLM", "openai"),
        )

        # Log raw return and attempt to extract the actual DPP JSON
        logger.debug(f"Template 16 — raw API returned string: {raw}")

        parsed = None
        try:
            parsed = _parse_json_response(raw)
        except Exception:
            # If top-level parse failed, try to load as plain json
            try:
                parsed = json.loads(raw)
            except Exception:
                logger.debug(
                    "Template 16 — could not parse top-level API response"
                )

        try:
            try:
                logger.debug(
                    f"Template 16 — parsed enrichment JSON: {json.dumps(parsed, ensure_ascii=False)}"
                )
            except Exception:
                logger.debug(
                    f"Template 16 — parsed enrichment (raw): {parsed}"
                )

            # Determine where the LLM-produced DPP lives.
            dpp_candidate = None

            def _try_parse(text):
                """Try _parse_json_response then json.loads, return dict or None."""
                if not isinstance(text, str) or not text.strip():
                    return None
                try:
                    result = _parse_json_response(text)
                    return result if isinstance(result, dict) else None
                except Exception:
                    pass
                try:
                    result = json.loads(text)
                    return result if isinstance(result, dict) else None
                except Exception:
                    pass
                # Last resort: extract JSON object from within the string
                match = re.search(r"\{[\s\S]*\}", text)
                if match:
                    try:
                        result = json.loads(match.group())
                        return result if isinstance(result, dict) else None
                    except Exception:
                        pass
                return None

            def _extract_from_result_list(res_list):
                """Extract DPP dict from vanilla API result list."""
                if not res_list:
                    return None
                first = res_list[0]
                if isinstance(first, dict):
                    # Already contains DPP keys directly
                    if any(
                        k in first
                        for k in ("1_identity", "2_colour_preference", "narrative")
                    ):
                        return first
                    # DPP is embedded as string in response/content/text
                    for key in ("response", "content", "text"):
                        inner_text = first.get(key)
                        if isinstance(inner_text, str) and inner_text.strip():
                            candidate = _try_parse(inner_text)
                            if candidate:
                                return candidate
                elif isinstance(first, str):
                    return _try_parse(first)
                return None

            # Case A: the parsed object already contains the DPP keys
            if isinstance(parsed, dict) and any(
                k in parsed
                for k in (
                    "1_identity",
                    "2_colour_preference",
                    "3_material_preference",
                    "narrative",
                )
            ):
                dpp_candidate = parsed

            # Case B: parsed is the result list directly (most common — _call_vanilla
            # serialises the result array to JSON so parsed becomes a Python list)
            elif isinstance(parsed, list):
                dpp_candidate = _extract_from_result_list(parsed)

            # Case C: the parsed object is the vanilla wrapper dict with 'success'/'result'
            elif isinstance(parsed, dict) and parsed.get("result"):
                res = parsed.get("result")
                if isinstance(res, list):
                    dpp_candidate = _extract_from_result_list(res)
                elif isinstance(res, str):
                    dpp_candidate = _try_parse(res)

            # If we couldn't find a DPP candidate, treat as failure
            if not dpp_candidate:
                # If the vanilla wrapper explicitly failed, include its message
                msg = None
                if isinstance(parsed, dict) and parsed.get("success") is False:
                    msg = parsed.get("message")
                raise Exception(
                    f"Template 16 did not return an enriched DPP. {msg or ''}"
                )

            # Merge returned DPP with existing raw_dpp (preserve originals when missing)
            raw_dpp["profile_version"] = dpp_candidate.get("profile_version", "v3.2")
            raw_dpp["1_identity"] = dpp_candidate.get(
                "1_identity", raw_dpp.get("1_identity", {})
            )
            raw_dpp["2_colour_preference"] = dpp_candidate.get(
                "2_colour_preference", raw_dpp.get("2_colour_preference", {})
            )
            raw_dpp["3_material_preference"] = dpp_candidate.get(
                "3_material_preference", raw_dpp.get("3_material_preference", {})
            )
            raw_dpp["4_finish_preference"] = dpp_candidate.get(
                "4_finish_preference", raw_dpp.get("4_finish_preference", {})
            )
            raw_dpp["5_pattern_tolerance"] = dpp_candidate.get(
                "5_pattern_tolerance", raw_dpp.get("5_pattern_tolerance", {})
            )
            raw_dpp["6_furniture_permanence"] = dpp_candidate.get(
                "6_furniture_permanence", raw_dpp.get("6_furniture_permanence", {})
            )
            raw_dpp["narrative"] = dpp_candidate.get(
                "narrative", raw_dpp.get("narrative", "")
            )
            raw_dpp["ai_prompt_injection"] = dpp_candidate.get(
                "ai_prompt_injection", raw_dpp.get("ai_prompt_injection", "")
            )
            raw_dpp["ai_enriched"] = True
            logger.info("DPP enrichment succeeded via Template 16")

        except Exception as e:
            logger.warning(f"DPP enrichment failed to extract DPP: {e}")
            raw_dpp["ai_enriched"] = False

    except Exception as e:
        logger.warning(f"DPP enrichment failed: {e} — keeping raw DPP")
        raw_dpp["ai_enriched"] = False

    return raw_dpp


def _build_enrichment_params(dpp: dict) -> dict:
    """Flatten DPP into {placeholder: value} strings for Template 16.
    When dpp.source is 'spotify', injects profile_origin_instruction so Template 16
    writes about music/playlists, not images or boards. Template 16 prompt in ProcessIQ
    must include: {profile_origin_instruction} and use {board_names} (we set it to
    'user's Spotify playlists (music)' for Spotify).
    """
    identity = dpp.get("1_identity", {}).get("archetypes", {})
    primary = identity.get("primary", {}) or {}
    secondary = identity.get("secondary", {}) or {}
    tertiary = identity.get("tertiary", {}) or {}
    materials = dpp.get("3_material_preference", {}).get("preferred_materials", [])
    colour_mood = dpp.get("2_colour_preference", {}).get("mood", "warm_neutral")
    # sampled_colours may be [{hex, label}] or plain hex strings — build readable list
    raw_colours = dpp.get("2_colour_preference", {}).get("sampled_colours", [])
    colours = []
    for c in raw_colours:
        if isinstance(c, dict) and c.get("hex"):
            label = c.get("label", "")
            colours.append(f"{c['hex']} ({label})" if label else c["hex"])
        elif isinstance(c, str) and c:
            colours.append(c)
    mood_tags = dpp.get("mood_tags", [])
    boards = dpp.get("board_summary", [])

    # user_selections — checkbox answers from the question card, formatted as:
    # "Image 1 (room.jpg): Warm layered linen textures, Soft ambient lighting.
    #  Image 2 (bedroom.jpg): Something Else - I love the layered textiles."
    user_selections = dpp.get("user_selections", "")

    # per_image_signals — rich per-image detail for Template 16 so it can reason
    # about each image individually (styles, materials, colours, mood, confidence).
    # Format: "Image 1 (file.jpg) [confidence 0.95]: styles=Luxury,Contemporary;
    #          materials=marble,gold metal trim; colours=#D9D6D1 soft marble white,
    #          #B89C8A muted beige; mood=elegant,refined,serene"
    per_image_signals = ""
    try:
        image_analyses = dpp.get("image_analyses", [])
        if image_analyses:
            parts = []
            for i, a in enumerate(image_analyses[:6], start=1):
                fname = a.get("filename", f"image{i}")
                conf = a.get("confidence", "?")
                styles = ", ".join(a.get("styles", []))
                mats = ", ".join(a.get("materials", []))
                colours_raw = a.get("dominant_colours", [])
                colour_strs = []
                for c in colours_raw[:3]:
                    if isinstance(c, dict) and c.get("hex"):
                        lbl = c.get("label", "")
                        colour_strs.append(f"{c['hex']} {lbl}".strip())
                    elif isinstance(c, str):
                        colour_strs.append(c)
                mood = ", ".join(a.get("mood_tags", [])[:3])
                parts.append(
                    f"Image {i} ({fname}) [conf {conf}]: "
                    f"styles={styles or 'unknown'}; "
                    f"materials={mats or 'unknown'}; "
                    f"colours={', '.join(colour_strs) or 'unknown'}; "
                    f"mood={mood or 'unknown'}"
                )
            per_image_signals = " | ".join(parts)
    except Exception:
        per_image_signals = ""

    dpp_source = dpp.get("source", "unknown")
    # When source is Spotify, give Template 16 explicit instructions and PLAYLIST/MUSIC context so the summary is about playlists, not generic interior design
    if dpp_source == "spotify":
        # Real playlist names so the model can say "From your Chill & Cozy and Warm & Natural playlists..."
        board_names_val = ", ".join(b["name"] for b in boards[:8] if b.get("name")) or "user's Spotify playlists"
        spotify_genres = dpp.get("spotify_top_genres") or []
        genres_str = ", ".join(spotify_genres[:12]) if isinstance(spotify_genres, list) else str(spotify_genres)
        mv = dpp.get("mood_vector") or {}
        mood_parts = []
        for key, label_left, label_right in [
            ("calm_energetic", "calm", "energetic"),
            ("warm_edgy", "warm", "edgy"),
            ("minimal_maximal", "minimal", "maximal"),
            ("vintage_modern", "vintage", "modern"),
        ]:
            v = mv.get(key)
            if v is not None:
                try:
                    pct = int(round(float(v) * 100))
                    mood_parts.append(f"{label_left}/{label_right} {pct}%")
                except (TypeError, ValueError):
                    pass
        spotify_mood_summary = "; ".join(mood_parts) if mood_parts else "inferred from music"
        profile_origin_instruction = (
            "CRITICAL — This profile is from SPOTIFY PLAYLISTS and MUSIC TASTE only (no images, no Pinterest). "
            "You MUST write the design summary (ai_prompt_injection) so it is clearly ABOUT THE USER'S PLAYLISTS AND MUSIC. "
            "Start or anchor the summary with their playlists and/or music: e.g. 'From your [playlist names] and your [genres] taste, your listening suggests...' or 'Your playlists point to a space that...'. "
            "Mention their playlists by name and/or genres. Use: 'from your music', 'your playlists', 'your listening', 'your taste suggests'. "
            "Do NOT write a generic interior-design-only summary (e.g. do not only list 'Design style: X, Materials: Y'). "
            "The summary must read as 'inferred from your playlists and music', not as if we analysed furniture images."
        )
        params_extra = {
            "spotify_playlist_names": board_names_val,
            "spotify_genres": genres_str or "not provided",
            "spotify_mood_summary": spotify_mood_summary,
        }
    else:
        profile_origin_instruction = (
            "This design profile was built from the user's Pinterest boards / uploaded images."
        )
        board_names_val = ", ".join(b["name"] for b in boards[:4] if b.get("name")) or "image upload"
        params_extra = {}

    params = {
        "primary_style": primary.get("name", "Contemporary"),
        "primary_weight": str(int(primary.get("weight", 0.55) * 100)),
        "secondary_style": secondary.get("name", "none"),
        "tertiary_style": tertiary.get("name", "none"),
        "materials": ", ".join(materials) if materials else "not detected",
        "colour_mood": colour_mood.replace("_", " "),
        "sampled_colours": ", ".join(colours[:5]) if colours else "not sampled",
        "mood_tags": ", ".join(mood_tags[:6]) if mood_tags else "not detected",
        "source": dpp_source,
        "board_names": board_names_val,
        "profile_origin_instruction": profile_origin_instruction,
    }
    params.update(params_extra)

    # Only inject user_selections if present — Template 16 prompt uses {user_selections}
    if user_selections:
        params["user_selections"] = user_selections

    # Only inject per_image_signals if we have them — gives T16 richer per-image context
    if per_image_signals:
        params["per_image_signals"] = per_image_signals

    # Inject material_dna and shape_dna if recorded from T21 / user slider adjustment
    msd = dpp.get("material_shape_dna", {})
    if msd:
        md = msd.get("material_dna", {})
        sd = msd.get("shape_dna", {})
        if md:
            params["material_dna"] = ", ".join(
                f"{k.replace('_', ' ')}={v:.2f}" for k, v in md.items()
            )
        if sd:
            params["shape_dna"] = ", ".join(
                f"{k.replace('_', ' ')}={v:.2f}" for k, v in sd.items()
            )

    return params


# ── Template 17: Design Suggestions ──────────────────────────────────────────


def generate_design_suggestions(dpp: dict, room_type: str = "living room") -> dict:
    """
    Call Template 17 to get 3 personalised room suggestions.

    Equivalent curl:
      curl -X POST .../vanilla_prompt_api
        -F template_id=17
        -F template_name="GaaZoo Design Suggestions"
        -F 'parameters={"ai_prompt_injection":"...","room_type":"living room",...}'
        -F llm=openai
    """
    identity = dpp.get("1_identity", {}).get("archetypes", {})
    primary = identity.get("primary", {}) or {}
    materials = dpp.get("3_material_preference", {}).get("preferred_materials", [])
    permanence = dpp.get("6_furniture_permanence", {}).get("level", "no_restriction")
    colours = dpp.get("2_colour_preference", {}).get("preferred_colours", [])
    colour_str = (
        ", ".join(f"{c['label']} ({c['hex']})" for c in colours[:3])
        if colours
        else "warm neutrals"
    )

    params = {
        "ai_prompt_injection": dpp.get("ai_prompt_injection", ""),
        "primary_style": primary.get("name", "Contemporary"),
        "materials": ", ".join(materials) if materials else "natural materials",
        "colour_palette": colour_str,
        "room_type": room_type,
        "permanence_note": (
            "IMPORTANT: Only suggest freestanding removable pieces — "
            "NO built-ins, NO wall-mounted items, NO permanent installation."
            if permanence == "removable_only"
            else ""
        ),
    }

    try:
        raw = _call_vanilla(
            template_id=17,
            template_name="GaaZoo Design Suggestions",
            parameters=params,
            llm=Config().get("VANILLA_LLM", "openai"),
        )
        return {
            "room_type": room_type,
            "narrative": raw,
            "dpp_summary": dpp.get("ai_prompt_injection", ""),
        }
    except Exception as e:
        logger.error(f"Design suggestions failed: {e}")
        raise


# ── Narrative (from stored DPP or re-enrich) ──────────────────────────────────


def generate_profile_narrative(dpp: dict) -> str:
    """Return existing narrative if present, else re-trigger enrichment."""
    if dpp.get("narrative") and dpp.get("ai_enriched"):
        return dpp["narrative"]
    enriched = enrich_dpp_with_ai(dpp)
    return enriched.get("narrative", "Profile narrative could not be generated.")


# ── Template 18: Design Q&A ───────────────────────────────────────────────────


def answer_design_question(dpp: dict, question: str) -> str:
    """
    Answer a free-form design question via Template 18.

    Equivalent curl:
      curl -X POST .../vanilla_prompt_api
        -F template_id=18
        -F template_name="GaaZoo Design Q&A"
        -F 'parameters={"ai_prompt_injection":"...","question":"What sofa suits me?"}'
        -F llm=openai
    """
    permanence = dpp.get("6_furniture_permanence", {}).get("level", "no_restriction")

    params = {
        "ai_prompt_injection": dpp.get("ai_prompt_injection", ""),
        "question": question,
        "permanence_note": (
            "Only suggest removable/freestanding pieces — no permanent installation."
            if permanence == "removable_only"
            else ""
        ),
    }

    try:
        return _call_vanilla(
            template_id=18,
            template_name="GaaZoo Design Q&A",
            parameters=params,
            llm=Config().get("VANILLA_LLM", "openai"),
        )
    except Exception as e:
        logger.error(f"Design Q&A failed: {e}")
        raise
