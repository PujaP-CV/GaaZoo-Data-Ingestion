"""
routes/profile_routes.py
-------------------------
DPP profile routes:
  GET  /profile/spotify/playlists  — list playlists (sample when no Premium)
  POST /profile/analyse/spotify   — Step 1: design signals + slider predictions
  POST /profile/build/spotify     — Step 2: build DPP from Spotify + answers

  ── Analyse only (Step 1 of 2) ──────────────────────────────────────────────
  POST /profile/analyse/images  — run Template 19/20 on uploaded images,
                                   return per-image analysis + question + options.
                                   No DPP built yet.
  POST /profile/analyse/boards  — same but downloads pin images from selected boards.

  ── Build (Step 2 of 2) ─────────────────────────────────────────────────────
  POST /profile/build/images    — accepts analyses + user checkbox answers,
                                   builds DPP and enriches via Template 16.
  POST /profile/build/boards    — same for Pinterest boards.
  GET  /profile/build           — Pinterest boards -> raw DPP -> AI enrichment
                                   (legacy one-shot flow, no question step).

  ── Utility ─────────────────────────────────────────────────────────────────
  GET    /profile/get     — return stored DPP
  GET    /profile/boards  — list Pinterest boards for selection UI
  DELETE /profile/clear   — reset
"""

import base64
import json as _json
import os

import requests
from flask import Blueprint, current_app, jsonify, request, session
from modules.dpp_builder import build_dpp_from_images, build_dpp_from_pinterest
from modules.gemini_ai import (
    analyse_single_image_vanilla,
    analyse_single_image_with_questions,
    enrich_dpp_with_ai,
    predict_material_shape_dna,
    predict_material_shape_dna_spotify,
    spotify_mood_to_attributes,
    spotify_mood_vector,
    spotify_question_from_signals,
)
from modules.spotify_auth import get_valid_spotify_token
from modules.spotify_api import (
    build_user_summary_from_live,
    fetch_user_playlists,
)
from modules.pinterest_fetcher import (
    PinterestAuthError,
    PinterestPermissionError,
    get_all_boards_with_pins,
    get_boards,
    get_user_profile,
)

profile_bp = Blueprint("profile", __name__)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGES = 10
MAX_MB = 5


# ── Shared helpers ────────────────────────────────────────────────────────────

def _validate_images(files: list) -> tuple:
    """Return (valid_files, skipped_messages) from a werkzeug file list."""
    valid, skipped = [], []
    for f in files[:MAX_IMAGES]:
        if f.content_type not in ALLOWED_TYPES:
            skipped.append(f"{f.filename}: unsupported type")
            continue
        f.seek(0, 2)
        size_mb = f.tell() / (1024 * 1024)
        f.seek(0)
        if size_mb > MAX_MB:
            skipped.append(f"{f.filename}: too large ({size_mb:.1f} MB)")
            continue
        valid.append(f)
    return valid, skipped


def _enrich_and_store(dpp: dict) -> dict:
    """Run AI enrichment on a raw DPP, store in session, return enriched DPP."""
    current_app.logger.info("Running AI enrichment on DPP...")
    try:
        dpp = enrich_dpp_with_ai(dpp)
    except Exception as e:
        current_app.logger.warning(f"AI enrichment skipped: {e}")
    try:
        safe = dict(dpp)
        if isinstance(safe.get("board_summary"), list):
            safe["board_summary"] = [
                {"name": b.get("name"), "pin_count": b.get("pin_count", 0), "image_url": None}
                for b in safe["board_summary"]
            ]
        session["dpp"] = safe
    except Exception:
        session["dpp"] = {
            "profile_version": dpp.get("profile_version"),
            "source": dpp.get("source"),
        }
    return dpp


def _build_user_selections_string(selections: list) -> str:
    """
    Convert frontend checkbox payload into a readable string for Template 16.
    """
    parts = []
    for i, s in enumerate(selections or [], start=1):
        fname = s.get("filename", f"image {i}")
        checked = [c for c in (s.get("checked") or []) if c]
        other = (s.get("other") or "").strip()
        if other:
            checked.append(f"Other: {other}")
        if checked:
            parts.append(f"Image {i} ({fname}): {', '.join(checked)}.")
    return " ".join(parts)


def _aggregate_image_signals(analyses: list) -> dict:
    """
    Merge per-image analysis dicts into one aggregated signal dict
    suitable for Template 21 (DNA Predictor).
    """
    styles, materials, mood_tags, colours = set(), set(), set(), []
    density_counts = {}
    for a in analyses:
        for s in a.get("styles", []):
            styles.add(s)
        for m in a.get("materials", []):
            materials.add(m)
        for t in a.get("mood_tags", []):
            mood_tags.add(t)
        for c in a.get("dominant_colours", [])[:2]:
            colours.append(c)
        d = a.get("spatial_density", "moderate")
        density_counts[d] = density_counts.get(d, 0) + 1
    dominant_density = max(density_counts, key=density_counts.get) if density_counts else "moderate"
    return {
        "styles": list(styles)[:8],
        "materials": list(materials)[:10],
        "mood_tags": list(mood_tags)[:8],
        "colours": colours[:6],
        "spatial_density": dominant_density,
    }


def _download_board_images(selected_boards: list, max_per_board: int = 4) -> tuple:
    """
    Download up to max_per_board pin images from each selected board.
    Returns (image_tuples, preview_list).
      image_tuples: [(filename, bytes, mimetype, pin_url), ...]
      preview_list: [{"name": fname, "image_url": url}, ...]
    """
    image_tuples, preview_list = [], []
    for b in selected_boards:
        count = 0
        for p in b.get("pins", [])[:12]:
            if count >= max_per_board:
                break
            url = p.get("image_url")
            if not url:
                continue
            try:
                r = requests.get(url, timeout=10)
                if not r.ok:
                    continue
                content = r.content
                if len(content) > MAX_MB * 1024 * 1024:
                    current_app.logger.debug(f"Skipping large pin image {url}")
                    continue
                fname = (
                    url.split("?")[0].rstrip("/").split("/")[-1]
                    or f"pin_{p.get('id')}.jpg"
                )
                mtype = r.headers.get("content-type", "image/jpeg")
                image_tuples.append((fname, content, mtype, url))
                preview_list.append({"name": fname, "image_url": url})
                count += 1
            except Exception as e:
                current_app.logger.debug(f"Failed to download pin image {url}: {e}")
    return image_tuples, preview_list


# ── POST /profile/analyse/images ──────────────────────────────────────────────

@profile_bp.route("/analyse/images", methods=["POST"])
def analyse_images_for_questions():
    """
    Step 1 of 2 — image upload flow.

    Runs Template 19 or 20 on each uploaded image.
    Returns per-image analysis + AI question + 4 options. No DPP built yet.

    Returns:
        { "success": true, "analyses": [{filename, image_url, question, options,
          dimension, styles, dominant_colours, ...}], "template_used": 19 }
    """
    files = request.files.getlist("images")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "Please upload at least one image."}), 400

    valid, skipped = _validate_images(files)
    if not valid:
        return jsonify({"error": "No valid images.", "skipped": skipped}), 400

    template_id = current_app.config.get("IMAGE_QUESTION_TEMPLATE_ID", 19)
    current_app.logger.info(
        f"Analysing {len(valid)} images with Template {template_id} (question mode)..."
    )

    analyses = []
    for f in valid:
        img_bytes = f.read()
        f.seek(0)
        result = analyse_single_image_with_questions(
            f.filename, img_bytes, f.content_type or "image/jpeg", template_id
        )
        # Attach base64 thumbnail so frontend can show image next to checkboxes
        try:
            f.seek(0)
            b64 = base64.b64encode(f.read()).decode()
            result["image_url"] = f"data:{f.content_type};base64,{b64}"
        except Exception:
            result["image_url"] = None
        analyses.append(result)

    # ── Template 21: predict DNA slider values from aggregated signals ────────
    slider_predictions = {}
    try:
        aggregated = _aggregate_image_signals(analyses)
        slider_predictions = predict_material_shape_dna(aggregated)
    except Exception as e:
        current_app.logger.warning(f"DNA slider prediction skipped: {e}")

    return jsonify({
        "success": True,
        "analyses": analyses,
        "skipped": skipped,
        "template_used": template_id,
        "slider_predictions": slider_predictions,
    })


# ── POST /profile/analyse/boards ──────────────────────────────────────────────

@profile_bp.route("/analyse/boards", methods=["POST"])
def analyse_boards_for_questions():
    """
    Step 1 of 2 — Pinterest boards flow.

    Downloads pin images from selected boards, runs Template 19/20 on each.
    Returns per-image analysis + question + options. No DPP built yet.

    POST body JSON: { "board_ids": ["id1", "id2"] }
    Returns same shape as /analyse/images.
    """
    if not session.get("pinterest_connected"):
        return jsonify({"error": "Pinterest not connected.", "reconnect": True}), 401

    data = request.get_json() or {}
    board_ids = data.get("board_ids") or []
    if not isinstance(board_ids, list) or not board_ids:
        return jsonify({"error": "Please provide board_ids as a non-empty list."}), 400

    token = session.get("pinterest_access_token")
    try:
        all_boards = get_all_boards_with_pins(token, max_boards=25, max_pins_per_board=50)
    except Exception as e:
        return jsonify({"error": f"Could not fetch boards: {e}"}), 500

    selected = [b for b in all_boards if b.get("id") in board_ids]
    if not selected:
        return jsonify({"error": "No matching boards found for given board_ids."}), 400

    image_tuples, _ = _download_board_images(selected, max_per_board=4)
    if not image_tuples:
        return jsonify({"error": "No images could be downloaded from selected boards."}), 500

    template_id = current_app.config.get("IMAGE_QUESTION_TEMPLATE_ID", 19)
    current_app.logger.info(
        f"Analysing {len(image_tuples)} board images with Template {template_id}..."
    )

    analyses = []
    for fname, img_bytes, mtype, pin_url in image_tuples:
        result = analyse_single_image_with_questions(fname, img_bytes, mtype, template_id)
        result["image_url"] = pin_url
        analyses.append(result)

    # ── Template 21: predict DNA slider values from aggregated signals ────────
    slider_predictions = {}
    try:
        aggregated = _aggregate_image_signals(analyses)
        slider_predictions = predict_material_shape_dna(aggregated)
    except Exception as e:
        current_app.logger.warning(f"DNA slider prediction skipped: {e}")

    return jsonify({
        "success": True,
        "analyses": analyses,
        "template_used": template_id,
        "slider_predictions": slider_predictions,
    })


# ── POST /profile/build/images ────────────────────────────────────────────────

@profile_bp.route("/build/images", methods=["POST"])
def build_profile_images():
    """
    Step 2 of 2 — image upload flow.

    Accepts multipart/form-data:
      images     — image files (re-analysed via T15 for raw DPP signals)
      selections — JSON string: [{"filename", "checked": [...], "other": "..."}]

    user_selections is embedded into the raw DPP before Template 16 enrichment.
    """
    from modules.image_analyser import analyse_images as _analyse_images

    files = request.files.getlist("images")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "Please upload at least one image."}), 400

    valid, skipped = _validate_images(files)
    if not valid:
        return jsonify({"error": "No valid images.", "skipped": skipped}), 400

    raw_sel = request.form.get("selections", "[]")
    try:
        selections = _json.loads(raw_sel)
    except Exception:
        selections = []

    # Read user-adjusted slider values from form
    raw_sliders = request.form.get("slider_values", "{}")
    try:
        slider_values = _json.loads(raw_sliders)
    except Exception:
        slider_values = {}

    current_app.logger.info(
        f"Building DPP from {len(valid)} images. "
        f"User selections provided: {bool(selections)} | "
        f"Slider values provided: {bool(slider_values)}"
    )

    try:
        analyses = _analyse_images(valid)
    except Exception as e:
        return jsonify({"error": f"Image analysis failed: {e}"}), 500

    try:
        previews = []
        for f in valid[:len(analyses)]:
            try:
                f.seek(0)
                content = f.read()
                previews.append({
                    "name": f.filename,
                    "image_url": (
                        f"data:{f.content_type};base64,"
                        f"{base64.b64encode(content).decode()}"
                    ),
                })
            except Exception:
                continue

        raw_dpp = build_dpp_from_images(analyses)
        raw_dpp["board_summary"] = previews
        # Store per-image analyses so _build_enrichment_params can pass rich
        # per-image signals (styles, materials, colours, mood) to Template 16.
        raw_dpp["image_analyses"] = analyses

        if selections:
            raw_dpp["user_selections"] = _build_user_selections_string(selections)

        # Store material/shape DNA with source tag
        if slider_values:
            raw_dpp["material_shape_dna"] = {
                "material_dna": slider_values.get("material_dna", {}),
                "shape_dna": slider_values.get("shape_dna", {}),
                "source": "ai_predicted+user_adjusted",
            }

        ok = raw_dpp.get("images_analyzed", 0)
        failed = raw_dpp.get("images_failed", 0)
        dpp = _enrich_and_store(raw_dpp)

        msg = f"Profile built from {ok} image{'s' if ok != 1 else ''}."
        if failed:
            msg += f" ({failed} failed.)"
        return jsonify({"success": True, "profile": dpp, "message": msg, "skipped": skipped})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── POST /profile/build/boards ────────────────────────────────────────────────

@profile_bp.route("/build/boards", methods=["POST"])
def build_profile_from_selected_boards():
    """
    Step 2 of 2 — Pinterest boards flow.

    POST body JSON:
      { "board_ids": ["id1","id2"], "selections": [{filename, checked, other}, ...] }
    """
    if not session.get("pinterest_connected"):
        return jsonify({"error": "Pinterest not connected.", "reconnect": True}), 401

    data = request.get_json() or {}
    board_ids = data.get("board_ids") or []
    selections = data.get("selections") or []
    slider_values = data.get("slider_values") or {}

    if not isinstance(board_ids, list) or not board_ids:
        return jsonify({"error": "Please provide board_ids as a non-empty list."}), 400

    token = session.get("pinterest_access_token")
    try:
        all_boards = get_all_boards_with_pins(token, max_boards=25, max_pins_per_board=50)
    except Exception as e:
        return jsonify({"error": f"Could not fetch boards: {e}"}), 500

    selected = [b for b in all_boards if b.get("id") in board_ids]
    if not selected:
        return jsonify({"error": "No matching boards found for given board_ids."}), 400

    image_tuples, preview_list = _download_board_images(selected, max_per_board=4)
    if not image_tuples:
        return jsonify({"error": "No images could be downloaded or analysed."}), 500

    analyses = []
    for fname, img_bytes, mtype, _ in image_tuples:
        try:
            ana = analyse_single_image_vanilla(fname, img_bytes, mtype)
            analyses.append(ana)
        except Exception as e:
            current_app.logger.debug(f"Image analysis failed for {fname}: {e}")

    if not analyses:
        return jsonify({"error": "All image analyses failed."}), 500

    try:
        raw_dpp = build_dpp_from_images(analyses)
        raw_dpp["source_boards"] = [b.get("name") for b in selected]
        raw_dpp["board_summary"] = preview_list
        # Store per-image analyses so _build_enrichment_params can pass rich
        # per-image signals (styles, materials, colours, mood) to Template 16.
        raw_dpp["image_analyses"] = analyses

        if selections:
            raw_dpp["user_selections"] = _build_user_selections_string(selections)

        # Store material/shape DNA with source tag
        if slider_values:
            raw_dpp["material_shape_dna"] = {
                "material_dna": slider_values.get("material_dna", {}),
                "shape_dna": slider_values.get("shape_dna", {}),
                "source": "ai_predicted+user_adjusted",
            }

        dpp = _enrich_and_store(raw_dpp)
        return jsonify({
            "success": True,
            "profile": dpp,
            "message": f"Profile built from {len(analyses)} analysed images.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── GET /profile/build (legacy Pinterest one-shot) ────────────────────────────

@profile_bp.route("/build")
def build_profile():
    """Legacy one-shot Pinterest DPP build — no question step."""
    if not session.get("pinterest_connected"):
        return jsonify({"error": "Pinterest not connected.", "reconnect": True}), 401

    token = session.get("pinterest_access_token")
    mode = session.get("pinterest_mode", "oauth")
    current_app.logger.info(f"Building DPP via Pinterest — mode: {mode}")

    try:
        user_info = get_user_profile(token)
        boards = get_all_boards_with_pins(token, max_boards=5, max_pins_per_board=20)

    except PinterestPermissionError as e:
        current_app.logger.error(f"403: {e}")
        return jsonify({
            "error": (
                "Pinterest trial access cannot read boards/pins. "
                "Go to developers.pinterest.com/apps -> click 'Upgrade access' "
                "and request Standard access. While waiting, use the image upload option instead."
            ),
            "error_type": "trial_access",
        }), 403

    except PinterestAuthError as e:
        current_app.logger.warning(f"401: {e}")
        if mode == "direct_token":
            return jsonify({
                "error": "Token expired. Generate a new one from developers.pinterest.com/apps.",
                "error_type": "token_expired",
                "reconnect": True,
            }), 401
        new_token = _try_refresh()
        if not new_token:
            return jsonify({"error": "Session expired. Please reconnect.", "reconnect": True}), 401
        try:
            user_info = get_user_profile(new_token)
            boards = get_all_boards_with_pins(new_token, max_boards=5, max_pins_per_board=20)
        except PinterestPermissionError:
            return jsonify({
                "error": "Trial access restriction. Use image upload instead.",
                "error_type": "trial_access",
            }), 403
        except Exception as e2:
            return jsonify({"error": str(e2), "reconnect": True}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        raw_dpp = build_dpp_from_pinterest(boards)
        raw_dpp["pinterest_user"] = session.get("pinterest_user") or user_info
        dpp = _enrich_and_store(raw_dpp)
        return jsonify({
            "success": True,
            "profile": dpp,
            "message": (
                f"Profile built from {raw_dpp.get('boards_analyzed', 0)} boards "
                f"and {raw_dpp.get('pins_analyzed', 0)} pins."
            ),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── GET /profile/get ──────────────────────────────────────────────────────────

@profile_bp.route("/get")
def get_profile():
    dpp = session.get("dpp")
    if not dpp:
        return jsonify({"error": "No profile yet."}), 404
    return jsonify({"profile": dpp})


# ── GET /profile/boards ───────────────────────────────────────────────────────

@profile_bp.route("/boards")
def list_boards():
    """Return a lightweight list of the user's Pinterest boards for the selection UI."""
    if not session.get("pinterest_connected"):
        return jsonify({"error": "Pinterest not connected.", "reconnect": True}), 401
    token = session.get("pinterest_access_token")
    try:
        boards = get_boards(token, max_boards=50)
        lightweight = [
            {
                "id": b.get("id"),
                "name": b.get("name"),
                "pin_count": b.get("pin_count"),
                "image_url": b.get("image_url"),
            }
            for b in boards
        ]
        return jsonify({"boards": lightweight})
    except PinterestAuthError as e:
        return jsonify({"error": str(e), "reconnect": True}), 401
    except PinterestPermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── GET /profile/spotify/playlists ────────────────────────────────────────────

def _load_sample_spotify_data():
    """Load sample Spotify JSON. Returns (playlists, data). data has playlists + optional user_summary / top_genres / audio_features."""
    path = os.path.join(os.path.dirname(__file__), "..", "data", "sample_spotify_playlists.json")
    if not os.path.exists(path):
        return [], {}
    with open(path, encoding="utf-8") as f:
        data = _json.load(f)
    playlists = data.get("playlists", [])
    return playlists, data


def _load_sample_spotify_playlists():
    playlists, _ = _load_sample_spotify_data()
    return playlists


@profile_bp.route("/spotify/playlists")
def list_spotify_playlists():
    """Return playlists: live from Spotify API when connected, else sample data."""
    if not session.get("spotify_connected"):
        return jsonify({"error": "Spotify not connected.", "reconnect": True}), 401

    token = get_valid_spotify_token()
    fallback_reason = None

    if not token:
        fallback_reason = "no_token"
        current_app.logger.warning(
            "Spotify playlists: no valid token (session may have no spotify_access_token or refresh failed). "
            "If the app is open on a different origin (e.g. port 3000), ensure CORS and cookies are sent."
        )
    else:
        try:
            live, api_status = fetch_user_playlists(token)
            if api_status == 403:
                return jsonify({
                    "error": "Spotify denied access to your playlists. Add your Spotify account in the Spotify Developer Dashboard (User Management) for this app.",
                    "reconnect": False,
                }), 403
            if live:
                return jsonify({"playlists": live, "source": "spotify_api"})
            fallback_reason = "empty_response" if api_status is None else f"http_{api_status}"
            current_app.logger.warning(
                "Spotify playlists: API returned no playlists (status=%s). Check scopes "
                "playlist-read-private, playlist-read-collaborative; or add user in Spotify Developer Dashboard.",
                api_status,
            )
        except Exception as e:
            fallback_reason = "api_error"
            current_app.logger.warning(f"Spotify API playlists failed: {e}")

    # Fallback: sample data
    playlists = _load_sample_spotify_playlists()
    lightweight = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "description": p.get("description"),
            "track_count": len(p.get("tracks", [])),
        }
        for p in playlists
    ]
    return jsonify({
        "playlists": lightweight,
        "source": "sample",
        "fallback_reason": fallback_reason,
    })


# ── POST /profile/analyse/spotify ─────────────────────────────────────────────

def _build_spotify_user_summary_params(playlists, selected_playlists, data):
    """
    Build (top_artists, top_tracks, top_genres, audio_features) for Template 24.
    Prefer user_summary from data; else derive from selected_playlists.
    """
    us = data.get("user_summary") or {}
    # Prefer user_summary; fallback to root-level (sample file may have top_genres/audio_features at root)
    top_artists_list = us.get("top_artists") or data.get("top_artists")
    top_tracks_list = us.get("top_tracks") or data.get("top_tracks")
    top_genres_list = us.get("top_genres") or data.get("top_genres")
    audio_features_obj = us.get("audio_features") or data.get("audio_features")

    if top_artists_list or top_tracks_list or top_genres_list:
        top_artists = ", ".join(top_artists_list[:15]) if isinstance(top_artists_list, list) else str(top_artists_list or "")
        if isinstance(top_tracks_list, list):
            top_tracks = "; ".join(
                f"{t.get('name', '')} – {t.get('artist', '')}" if isinstance(t, dict) else str(t)
                for t in top_tracks_list[:20]
            )
        else:
            top_tracks = str(top_tracks_list or "")
        top_genres = ", ".join(top_genres_list[:15]) if isinstance(top_genres_list, list) else str(top_genres_list or "")
        audio_features_str = _json.dumps(audio_features_obj) if audio_features_obj else "not provided"
        return top_artists, top_tracks, top_genres, audio_features_str

    # Derive from selected playlists
    artists = set()
    tracks_lines = []
    genres = set()
    for p in selected_playlists:
        for t in p.get("tracks", [])[:15]:
            artists.add(t.get("artist", ""))
            genres.add(t.get("genre", ""))
            tracks_lines.append(f"{t.get('name', '')} – {t.get('artist', '')}" + (f" ({t.get('genre', '')})" if t.get("genre") else ""))
    top_artists = ", ".join(sorted(artists - {""})[:15]) or "unknown"
    top_tracks = "\n".join(tracks_lines[:25]) or "unknown"
    top_genres = ", ".join(sorted(genres - {""})[:15]) or "unknown"
    audio_features_str = "not provided"
    return top_artists, top_tracks, top_genres, audio_features_str


def _spotify_signal_to_analysis(signals: dict, playlist_names: str = "Spotify playlists") -> dict:
    """Turn Template 25 output into one 'analysis' object for question card + build_dpp_from_images.

    Uses Template 26 (spotify_question_from_signals) to generate a playlist-aware question and options.
    Falls back to a simple fixed question if Template 26 fails.
    """
    styles = signals.get("styles", [])[:4]
    mood_tags = signals.get("mood_tags", [])
    # Default question/options (fallback)
    question = "Based on your playlists and music, which design direction speaks to you most?"
    options = [f"The {s} vibe" for s in styles] if styles else [
        "Calm and minimal",
        "Warm and layered",
        "Bold and expressive",
        "Natural and organic",
    ]
    try:
        q_res = spotify_question_from_signals(styles, mood_tags, playlist_names)
        q_text = (q_res or {}).get("question") or ""
        q_opts = (q_res or {}).get("options") or []
        if q_text.strip() and q_opts:
            question = q_text.strip()
            options = q_opts[:4]
    except Exception:
        # Keep fallback
        pass
    return {
        "filename": "Spotify playlists",
        "image_url": "",
        "styles": signals.get("styles", []),
        "dominant_colours": signals.get("colours", []) or signals.get("dominant_colours", []),
        "materials": signals.get("materials", []),
        "mood_tags": signals.get("mood_tags", []),
        "spatial_density": signals.get("spatial_density", "moderate"),
        "confidence": float(signals.get("confidence", 0.8)),
        "question": question,
        "options": options[:4],
    }


@profile_bp.route("/analyse/spotify", methods=["POST"])
def analyse_spotify():
    """
    Step 1 of 2 — Spotify flow. Uses Template 24 → 25 → 23.
    When connected with Premium: fetches live playlists/tracks/audio features from Spotify API.
    Otherwise uses sample data or derived from sample playlists.
    """
    if not session.get("spotify_connected"):
        return jsonify({"error": "Spotify not connected.", "reconnect": True}), 401

    data = request.get_json() or {}
    playlist_ids = data.get("playlist_ids") or []

    token = get_valid_spotify_token()
    if token and playlist_ids:
        # Live API: fetch tracks and audio features for selected playlists
        try:
            live_playlists, _ = fetch_user_playlists(token)
            id_to_name = {p["id"]: p["name"] for p in live_playlists if p.get("id") and p.get("name")}
            playlist_names = [id_to_name.get(pid, "") for pid in playlist_ids if id_to_name.get(pid) is not None]
            if not playlist_names:
                playlist_names = [id_to_name.get(pid, f"Playlist {pid}") for pid in playlist_ids]
            result, live_error = build_user_summary_from_live(token, playlist_ids, playlist_names)
            if live_error:
                return jsonify({"error": live_error}), 403 if "Forbidden" in live_error else 400
            if result:
                top_artists, top_tracks, top_genres, audio_features_str, top_genres_list = result
                session["spotify_playlist_names"] = playlist_names
                session["spotify_top_genres"] = top_genres_list
                try:
                    mood_vector = spotify_mood_vector(top_artists, top_tracks, top_genres, audio_features_str)
                    signals = spotify_mood_to_attributes(mood_vector)
                except Exception as e:
                    current_app.logger.exception(e)
                    return jsonify({"error": f"Design analysis failed: {e}"}), 500
                analysis = _spotify_signal_to_analysis(signals, ", ".join(playlist_names))
                slider_predictions = {}
                try:
                    aggregated = {
                        "styles": signals.get("styles", []),
                        "materials": signals.get("materials", []),
                        "colours": signals.get("colours", []) or signals.get("dominant_colours", []),
                        "mood_tags": signals.get("mood_tags", []),
                        "spatial_density": signals.get("spatial_density", "moderate"),
                    }
                    slider_predictions = predict_material_shape_dna_spotify(aggregated)
                except Exception as e:
                    current_app.logger.warning(f"Spotify DNA prediction skipped: {e}")
                session["spotify_analyses"] = [analysis]
                session["spotify_slider_predictions"] = slider_predictions
                session["spotify_playlist_ids"] = playlist_ids
                session["spotify_mood_vector"] = mood_vector
                return jsonify({
                    "success": True,
                    "analyses": [analysis],
                    "slider_predictions": slider_predictions,
                    "playlist_ids": playlist_ids,
                    "source": "spotify_api",
                })
        except Exception as e:
            current_app.logger.warning(f"Spotify live analyse failed, falling back to sample: {e}")

    # Sample data or no playlist_ids
    playlists, spotify_data = _load_sample_spotify_data()
    if not playlists:
        return jsonify({"error": "No playlists available. Connect Spotify and select playlists."}), 500

    if playlist_ids:
        selected = [p for p in playlists if p.get("id") in playlist_ids]
    else:
        selected = playlists

    if not selected:
        return jsonify({"error": "No playlists selected."}), 400

    top_artists, top_tracks, top_genres, audio_features_str = _build_spotify_user_summary_params(
        playlists, selected, spotify_data
    )

    try:
        mood_vector = spotify_mood_vector(top_artists, top_tracks, top_genres, audio_features_str)
        signals = spotify_mood_to_attributes(mood_vector)
    except Exception as e:
        current_app.logger.exception(e)
        return jsonify({"error": f"Design analysis failed: {e}"}), 500

    playlist_names = [p.get("name", "") for p in selected if p.get("name")]
    analysis = _spotify_signal_to_analysis(signals, ", ".join(playlist_names) or "Spotify playlists")

    slider_predictions = {}
    try:
        aggregated = {
            "styles": signals.get("styles", []),
            "materials": signals.get("materials", []),
            "colours": signals.get("colours", []) or signals.get("dominant_colours", []),
            "mood_tags": signals.get("mood_tags", []),
            "spatial_density": signals.get("spatial_density", "moderate"),
        }
        slider_predictions = predict_material_shape_dna_spotify(aggregated)
    except Exception as e:
        current_app.logger.warning(f"Spotify DNA prediction skipped: {e}")

    session["spotify_analyses"] = [analysis]
    session["spotify_slider_predictions"] = slider_predictions
    session["spotify_playlist_ids"] = [p.get("id") for p in selected]
    session["spotify_playlist_names"] = [p.get("name", "") for p in selected if p.get("name")]
    session["spotify_mood_vector"] = mood_vector
    us = spotify_data.get("user_summary") or {}
    session["spotify_top_genres"] = us.get("top_genres") or spotify_data.get("top_genres") or []

    return jsonify({
        "success": True,
        "analyses": [analysis],
        "slider_predictions": slider_predictions,
        "playlist_ids": session["spotify_playlist_ids"],
    })


# ── POST /profile/build/spotify ───────────────────────────────────────────────

@profile_bp.route("/build/spotify", methods=["POST"])
def build_profile_spotify():
    """
    Step 2 of 2 — Spotify flow. Build DPP from stored Spotify analysis + selections + sliders.
    """
    if not session.get("spotify_connected"):
        return jsonify({"error": "Spotify not connected.", "reconnect": True}), 401

    analyses = session.get("spotify_analyses") or []
    if not analyses:
        return jsonify({"error": "Run analyse/spotify first (Step 1).", "reconnect": False}), 400

    data = request.get_json() or {}
    selections = data.get("selections") or []
    slider_values = data.get("slider_values") or {}

    try:
        raw_dpp = build_dpp_from_images(analyses)
        raw_dpp["source"] = "spotify"
        playlist_names = session.get("spotify_playlist_names") or []
        raw_dpp["board_summary"] = [{"name": n, "pin_count": 0, "image_url": ""} for n in (playlist_names if playlist_names else ["Spotify playlists"])]
        raw_dpp["image_analyses"] = analyses
        raw_dpp["spotify_top_genres"] = session.get("spotify_top_genres") or []
        mood_vector = session.get("spotify_mood_vector")
        if mood_vector:
            raw_dpp["mood_vector"] = mood_vector

        if selections:
            raw_dpp["user_selections"] = _build_user_selections_string(selections)

        if slider_values:
            raw_dpp["material_shape_dna"] = {
                "material_dna": slider_values.get("material_dna", {}),
                "shape_dna": slider_values.get("shape_dna", {}),
                "source": "ai_predicted+user_adjusted",
            }

        dpp = _enrich_and_store(raw_dpp)
        dpp["source"] = "spotify"
        dpp["board_summary"] = raw_dpp.get("board_summary") or [{"name": "Spotify playlists", "pin_count": 0, "image_url": ""}]
        if mood_vector:
            dpp["mood_vector"] = mood_vector
        session.pop("spotify_analyses", None)
        session.pop("spotify_slider_predictions", None)
        session.pop("spotify_playlist_ids", None)
        session.pop("spotify_playlist_names", None)
        session.pop("spotify_mood_vector", None)
        session.pop("spotify_top_genres", None)

        return jsonify({
            "success": True,
            "profile": dpp,
            "message": "Profile built from your Spotify taste.",
        })
    except Exception as e:
        current_app.logger.exception(e)
        return jsonify({"error": str(e)}), 500


# ── DELETE /profile/clear ─────────────────────────────────────────────────────

@profile_bp.route("/clear", methods=["DELETE"])
def clear_profile():
    session.pop("dpp", None)
    return jsonify({"message": "Profile cleared."})


# ── Private ───────────────────────────────────────────────────────────────────

def _try_refresh():
    from modules.pinterest_auth import refresh_access_token
    rt = session.get("pinterest_refresh_token")
    if not rt:
        return None
    try:
        new = refresh_access_token(rt)
        session["pinterest_access_token"] = new["access_token"]
        session["pinterest_refresh_token"] = new.get("refresh_token", rt)
        return new["access_token"]
    except Exception as e:
        current_app.logger.error(f"Refresh failed: {e}")
        return None
