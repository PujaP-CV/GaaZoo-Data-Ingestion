"""
Viewer routes — 3D Catalog Viewer module.
Handles: frontend serving, Meshy GLB proxy, single-image → 3D upload.
"""

import base64
import time
import uuid
from pathlib import Path

import requests
from flask import Blueprint, Response, jsonify, request, send_from_directory

from config import DATA_DIR, DIR_3D, DIR_3D_SCALED, DIR_TEMP, MESHY_API_KEY, MESHY_BASE
from modules.model_scaler import get_model_dimensions, scale_model, scale_model_by_percent

viewer_bp = Blueprint("viewer", __name__)

ROOT = Path(__file__).resolve().parent.parent.parent   # repo root


# ── Frontend serving ───────────────────────────────────────────────────

@viewer_bp.route("/")
def index():
    frontend_dir = ROOT / "frontend"
    return send_from_directory(str(frontend_dir), "index.html")


@viewer_bp.route("/dpp")
def dpp():
    """Serve the Design Personality Profile frontend."""
    frontend_dir = ROOT / "frontend"
    return send_from_directory(str(frontend_dir), "dpp.html")


# ── Meshy GLB proxy (CORS bypass) ─────────────────────────────────────

@viewer_bp.route("/proxy-glb")
def proxy_glb():
    url = request.args.get("url")
    if not url or not url.startswith("https://"):
        return jsonify({"error": "Missing or invalid url"}), 400
    try:
        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()
        return Response(
            r.iter_content(chunk_size=8192),
            content_type=r.headers.get("Content-Type", "model/gltf-binary"),
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502


# ── Single image → 3D (direct upload) ────────────────────────────────

def _task_from_status_response(status_data):
    if "result" in status_data and isinstance(status_data["result"], dict):
        return status_data["result"]
    return status_data


@viewer_bp.route("/generate-3d", methods=["POST"])
def generate_3d():
    if not MESHY_API_KEY:
        return jsonify({"error": "MESHY_API_KEY is not set. Add it to your .env file."}), 500

    image = request.files.get("image")
    if not image:
        return jsonify({"error": "No image provided"}), 400

    obj_width  = request.form.get("obj_width",  "").strip() or None
    obj_height = request.form.get("obj_height", "").strip() or None
    obj_depth  = request.form.get("obj_depth",  "").strip() or None
    obj_unit   = request.form.get("obj_unit",   "cm").strip() or "cm"
    has_dims   = any([obj_width, obj_height, obj_depth])

    raw      = image.read()
    b64      = base64.b64encode(raw).decode("utf-8")
    mime     = image.content_type or "image/jpeg"
    image_url = f"data:{mime};base64,{b64}"

    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type":  "application/json",
    }
    response = requests.post(MESHY_BASE, headers=headers, json={"image_url": image_url})
    data     = response.json()

    task_id = data.get("result")
    if not task_id:
        msg  = data.get("message", data.get("error", "Failed to create task"))
        code = 400 if response.status_code < 500 else 502
        return jsonify({"error": str(msg)}), code

    status_url      = f"{MESHY_BASE}/{task_id}"
    timeout_seconds = 300
    started         = time.time()
    poll_headers    = {**headers, "Connection": "close"}
    max_retries     = 3

    while True:
        if time.time() - started > timeout_seconds:
            return jsonify({"error": "Conversion timed out after 5 minutes. Try again or use a simpler image."}), 504

        last_error = None
        for attempt in range(max_retries):
            try:
                status_response = requests.get(status_url, headers=poll_headers, timeout=30)
                break
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(2)
        else:
            return jsonify({"error": "Connection to 3D service was reset. Please try again."}), 502

        try:
            status_data = status_response.json()
        except Exception:
            return jsonify({"error": "Invalid response from 3D service"}), 502

        task   = _task_from_status_response(status_data)
        status = task.get("status")

        if status == "SUCCEEDED":
            model_urls = task.get("model_urls") or {}
            model_url  = model_urls.get("glb")
            if not model_url:
                return jsonify({"error": "No model URL in response", "task": task}), 500

            scaled_model_url = None
            scale_info       = None

            if has_dims:
                try:
                    from modules.model_scaler import scale_model
                    glb_id      = uuid.uuid4().hex[:12]
                    orig_path   = Path(str(DIR_3D)) / f"upload_{glb_id}_orig.glb"
                    scaled_path = Path(str(DIR_3D)) / f"upload_{glb_id}_scaled.glb"

                    glb_r = requests.get(model_url, timeout=60)
                    glb_r.raise_for_status()
                    orig_path.parent.mkdir(parents=True, exist_ok=True)
                    orig_path.write_bytes(glb_r.content)

                    scale_info = scale_model(
                        str(orig_path), str(scaled_path),
                        width  = float(obj_width)  if obj_width  else None,
                        height = float(obj_height) if obj_height else None,
                        depth  = float(obj_depth)  if obj_depth  else None,
                        unit   = obj_unit,
                    )

                    try:
                        rel = scaled_path.resolve().relative_to(Path(str(DATA_DIR)).resolve())
                        scaled_model_url = "/api/files/" + str(rel).replace("\\", "/")
                    except ValueError:
                        pass
                except Exception as exc:
                    scale_info = {"error": str(exc)}

            return jsonify({
                "model_url":        model_url,
                "scaled_model_url": scaled_model_url,
                "scale_info":       scale_info,
                "task":             task,
            })

        if status == "FAILED":
            err = (task.get("task_error") or {}).get("message", "Generation failed")
            return jsonify({"error": err, "task": task}), 400

        if status not in ("PENDING", "IN_PROGRESS", None):
            return jsonify({"error": f"Unexpected status: {status}"}), 500

        time.sleep(2)


# ── Load 3D model: upload GLB/OBJ and scale or resize (per Amal: 1 param = scale, 2–3 = resize) ──

ALLOWED_3D_EXT = (".glb", ".obj")


@viewer_bp.route("/3d-dimensions", methods=["POST"])
def get_3d_dimensions():
    """Return bounding box dimensions of an uploaded 3D file (no scaling, no storage)."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_3D_EXT:
        return jsonify({"error": f"Only .glb and .obj are supported. Got: {ext or 'no extension'}"}), 400
    unit = (request.form.get("obj_unit") or "cm").strip() or "cm"
    load_id = uuid.uuid4().hex[:12]
    path = Path(str(DIR_TEMP)) / f"dim_{load_id}_orig{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        file.save(str(path))
        dims = get_model_dimensions(str(path), unit=unit)
        return jsonify(dims)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@viewer_bp.route("/scale-3d", methods=["POST"])
def scale_3d():
    """Upload a .glb or .obj file and optionally scale (1 dim) or resize (2–3 dims) to real-world dimensions."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_3D_EXT:
        return jsonify({"error": f"Only .glb and .obj are supported. Got: {ext or 'no extension'}"}), 400
    obj_width   = request.form.get("obj_width",   "").strip() or None
    obj_height  = request.form.get("obj_height",  "").strip() or None
    obj_depth   = request.form.get("obj_depth",   "").strip() or None
    obj_unit    = request.form.get("obj_unit",    "cm").strip() or "cm"
    scale_pct   = request.form.get("scale_percent", "").strip() or None
    scale_dir   = (request.form.get("scale_direction", "").strip() or "increase").lower()
    use_percent = request.form.get("scale_by_percent") == "1" and scale_pct
    has_dims    = any([obj_width, obj_height, obj_depth]) or use_percent

    load_id = uuid.uuid4().hex[:12]

    def _file_url(path: Path) -> str:
        try:
            rel = path.resolve().relative_to(Path(str(DATA_DIR)).resolve())
            return "/api/files/" + str(rel).replace("\\", "/")
        except ValueError:
            return ""

    # Use temp dir for upload; only the scaled result is stored under data/3d/scaled/
    orig_path = Path(str(DIR_TEMP)) / f"load_{load_id}_orig{ext}"
    orig_path.parent.mkdir(parents=True, exist_ok=True)
    file.save(str(orig_path))

    model_url = None
    scaled_model_url = None
    scale_info = None

    if has_dims:
        scaled_path = Path(str(DIR_3D_SCALED)) / f"load_{load_id}_scaled.glb"
        try:
            if use_percent:
                scale_info = scale_model_by_percent(
                    str(orig_path), str(scaled_path),
                    percent=float(scale_pct), direction=scale_dir,
                )
            else:
                scale_info = scale_model(
                    str(orig_path), str(scaled_path),
                    width  = float(obj_width)  if obj_width  else None,
                    height = float(obj_height) if obj_height else None,
                    depth  = float(obj_depth)  if obj_depth  else None,
                    unit   = obj_unit,
                )
            scaled_model_url = _file_url(scaled_path)
        except Exception as e:
            scale_info = {"error": str(e)}
        finally:
            try:
                orig_path.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        # No scaling: serve from temp for viewing only (not stored under 3d/)
        model_url = _file_url(orig_path)

    return jsonify({
        "model_url":        model_url,
        "scaled_model_url": scaled_model_url,
        "scale_info":       scale_info,
    })
