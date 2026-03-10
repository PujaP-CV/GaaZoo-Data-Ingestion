"""
Viewer routes — 3D Catalog Viewer module.
Handles: frontend serving, Meshy GLB proxy, single-image → 3D upload, GLB scaling.
"""

import base64
import time
import uuid
from pathlib import Path

import requests
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from config import DATA_DIR, DIR_3D, DIR_3D_SCALED, DIR_TEMP, MESHY_API_KEY, MESHY_BASE

router = APIRouter()
ROOT   = Path(__file__).resolve().parent.parent.parent   # repo root


# ── Frontend serving ───────────────────────────────────────────────────

@router.get("/")
def index():
    """Serve index.html (used when Flask/uvicorn also serves the frontend)."""
    return FileResponse(str(ROOT / "frontend" / "index.html"))


@router.get("/dpp")
def dpp_page():
    """Serve the Design Personality Profile page."""
    return FileResponse(str(ROOT / "frontend" / "dpp.html"))


# ── Meshy GLB proxy (bypass CORS on Meshy CDN) ────────────────────────

@router.get("/proxy-glb")
def proxy_glb(url: str):
    if not url or not url.startswith("https://"):
        return JSONResponse({"error": "Missing or invalid url"}, status_code=400)
    try:
        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()
        return StreamingResponse(
            r.iter_content(chunk_size=8192),
            media_type=r.headers.get("Content-Type", "model/gltf-binary"),
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except requests.RequestException as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ── Single image → 3D (direct upload via Meshy) ───────────────────────

def _task_from_status(data: dict) -> dict:
    if "result" in data and isinstance(data["result"], dict):
        return data["result"]
    return data


@router.post("/generate-3d")
async def generate_3d(
    image:      UploadFile = File(...),
    obj_width:  str        = Form(""),
    obj_height: str        = Form(""),
    obj_depth:  str        = Form(""),
    obj_unit:   str        = Form("cm"),
):
    if not MESHY_API_KEY:
        return JSONResponse({"error": "MESHY_API_KEY is not set. Add it to your .env file."}, status_code=500)

    obj_width  = obj_width.strip()  or None
    obj_height = obj_height.strip() or None
    obj_depth  = obj_depth.strip()  or None
    obj_unit   = obj_unit.strip()   or "cm"
    has_dims   = any([obj_width, obj_height, obj_depth])

    raw       = await image.read()
    b64       = base64.b64encode(raw).decode("utf-8")
    mime      = image.content_type or "image/jpeg"
    image_url = f"data:{mime};base64,{b64}"

    headers  = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(MESHY_BASE, headers=headers, json={"image_url": image_url})
    data     = response.json()

    task_id = data.get("result")
    if not task_id:
        msg  = data.get("message", data.get("error", "Failed to create task"))
        code = 400 if response.status_code < 500 else 502
        return JSONResponse({"error": str(msg)}, status_code=code)

    status_url      = f"{MESHY_BASE}/{task_id}"
    timeout_seconds = 300
    started         = time.time()
    poll_headers    = {**headers, "Connection": "close"}

    while True:
        if time.time() - started > timeout_seconds:
            return JSONResponse({"error": "Conversion timed out after 5 minutes. Try again or use a simpler image."}, status_code=504)

        for attempt in range(3):
            try:
                status_response = requests.get(status_url, headers=poll_headers, timeout=30)
                break
            except requests.RequestException:
                if attempt < 2:
                    time.sleep(2)
        else:
            return JSONResponse({"error": "Connection to 3D service was reset. Please try again."}, status_code=502)

        try:
            status_data = status_response.json()
        except Exception:
            return JSONResponse({"error": "Invalid response from 3D service"}, status_code=502)

        task   = _task_from_status(status_data)
        status = task.get("status")

        if status == "SUCCEEDED":
            model_urls = task.get("model_urls") or {}
            model_url  = model_urls.get("glb")
            if not model_url:
                return JSONResponse({"error": "No model URL in response", "task": task}, status_code=500)

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

            return {"model_url": model_url, "scaled_model_url": scaled_model_url, "scale_info": scale_info, "task": task}

        if status == "FAILED":
            err = (task.get("task_error") or {}).get("message", "Generation failed")
            return JSONResponse({"error": err, "task": task}, status_code=400)

        if status not in ("PENDING", "IN_PROGRESS", None):
            return JSONResponse({"error": f"Unexpected status: {status}"}, status_code=500)

        time.sleep(2)


# ── Load / scale existing 3D model ────────────────────────────────────

ALLOWED_3D_EXT = (".glb", ".obj")


@router.post("/scale-3d")
async def scale_3d(
    file:       UploadFile = File(...),
    obj_width:  str        = Form(""),
    obj_height: str        = Form(""),
    obj_depth:  str        = Form(""),
    obj_unit:   str        = Form("cm"),
):
    """Upload a .glb or .obj and optionally scale to real-world dimensions."""
    if not file or not file.filename:
        return JSONResponse({"error": "No file provided"}, status_code=400)
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_3D_EXT:
        return JSONResponse({"error": f"Only .glb and .obj are supported. Got: {ext or 'no extension'}"}, status_code=400)

    obj_width  = obj_width.strip()  or None
    obj_height = obj_height.strip() or None
    obj_depth  = obj_depth.strip()  or None
    obj_unit   = obj_unit.strip()   or "cm"
    has_dims   = any([obj_width, obj_height, obj_depth])

    load_id   = uuid.uuid4().hex[:12]
    orig_path = Path(str(DIR_TEMP)) / f"load_{load_id}_orig{ext}"
    orig_path.parent.mkdir(parents=True, exist_ok=True)
    orig_path.write_bytes(await file.read())

    def _file_url(p: Path) -> str:
        try:
            rel = p.resolve().relative_to(Path(str(DATA_DIR)).resolve())
            return "/api/files/" + str(rel).replace("\\", "/")
        except ValueError:
            return ""

    model_url        = None
    scaled_model_url = None
    scale_info       = None

    if has_dims:
        scaled_path = Path(str(DIR_3D_SCALED)) / f"load_{load_id}_scaled.glb"
        try:
            from modules.model_scaler import scale_model
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
        model_url = _file_url(orig_path)

    return {"model_url": model_url, "scaled_model_url": scaled_model_url, "scale_info": scale_info}
