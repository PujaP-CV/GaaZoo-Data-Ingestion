"""
Catalog routes — Image Pipeline module.
Handles: catalog CRUD, image fetching (Amazon / Google), local vendor upload,
         3D conversion pipeline triggers, and static file serving.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from config import DATA_DIR, DIR_2D, DIR_3D, DIR_DOLLHOUSE
from modules.catalog_db import (
    init_db, list_items, get_item_by_asin, upsert_item,
    upsert_dollhouse, list_dollhouses, get_dollhouse,
)

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────

def _path_to_file_url(path) -> Optional[str]:
    """If path is under DATA_DIR, return /api/files/<relative>. Else None."""
    if not path:
        return None
    p = Path(path)
    try:
        rel = p.resolve().relative_to(Path(DATA_DIR).resolve())
        return "/api/files/" + str(rel).replace("\\", "/")
    except ValueError:
        return None


def _enrich(item: dict) -> dict:
    item["image_url_original"] = item.get("image_url")
    item["image_url"] = _path_to_file_url(item.get("image_path_used")) or item.get("image_url")
    item["glb_url"]   = _path_to_file_url(item.get("glb_path"))
    paths = item.get("image_paths") or []
    if isinstance(paths, str):
        try:
            paths = json.loads(paths)
        except Exception:
            paths = []
    item["image_gallery_urls"] = [u for p in paths if p for u in [_path_to_file_url(p)] if u]
    if not item["image_gallery_urls"] and item.get("image_url"):
        item["image_gallery_urls"] = [item["image_url"]]
    return item


# ── Catalog CRUD ───────────────────────────────────────────────────────

@router.get("/api/catalog")
def api_catalog(status: Optional[str] = None, limit: int = 100, offset: int = 0):
    limit = min(limit, 200)
    # "all" or empty means no filter; only pending/succeeded/failed filter by conversion_status
    conversion_filter = None
    if status and str(status).strip().lower() not in ("", "all"):
        conversion_filter = str(status).strip()
    try:
        items = list_items(conversion_status=conversion_filter, limit=limit, offset=offset)
        result = {"items": [_enrich(it) for it in items]}
        if not result["items"]:
            result["message"] = (
                "Catalog is empty. Add items via Data Ingestion (Amazon/Google) or Add local vendor."
            )
        return result
    except Exception as e:
        return JSONResponse({
            "items": [],
            "warning": "Catalog database is unavailable. Start Neo4j and check NEO4J_URI / NEO4J_PASSWORD in .env.",
            "detail": str(e),
        }, status_code=200)


@router.get("/api/catalog/{asin}")
def api_catalog_item(asin: str):
    item = get_item_by_asin(asin)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return _enrich(item)


@router.delete("/api/catalog/{asin}")
def api_catalog_delete(asin: str, files: str = "1"):
    from modules.catalog_db import delete_item
    delete_files = files.strip().lower() not in ("0", "false", "no")
    if delete_item(asin, delete_files=delete_files):
        return {"ok": True, "asin": asin}
    raise HTTPException(status_code=404, detail="Not found")


# ── Fetch images (Amazon / Google SERP → Neo4j) ────────────────────────

@router.post("/api/fetch-images")
async def api_fetch_images(request: Request):
    data   = await request.json()
    source = (data.get("source") or "").strip().lower() or "amazon"
    if source not in ("amazon", "google"):
        return JSONResponse({"error": "source must be 'amazon' or 'google'"}, status_code=400)

    query = data.get("query") or ""
    if not query:
        return JSONResponse({"error": "Missing 'query'"}, status_code=400)

    country                = (data.get("country") or "IN").strip() or "IN"
    max_amazon             = int(data.get("max_amazon", 5))
    num_serp               = int(data.get("num_serp", 10))
    max_images_per_product = int(data.get("max_images_per_product", 3))
    serp_country           = country.lower() if len(country) == 2 else "in"

    all_items, errors = [], []
    amazon_count = serp_count = 0

    if source == "amazon":
        try:
            from pipelines.pipeline_amazon import run_amazon_pipeline
            from config import RAPIDAPI_KEY
            if RAPIDAPI_KEY:
                result = run_amazon_pipeline(
                    query=query, country=country,
                    max_products=max_amazon,
                    max_images_per_product=max_images_per_product,
                )
                for it in result:
                    it["source"] = "amazon"
                    all_items.append(it)
                amazon_count = len(result)
            else:
                errors.append("RAPIDAPI_KEY not set")
        except Exception as e:
            errors.append(f"Amazon: {e}")

    if source == "google":
        try:
            from pipelines.pipeline_serp import run_serp_pipeline
            from config import SERPAPI_KEY
            if SERPAPI_KEY:
                result = run_serp_pipeline(
                    query=query, num=num_serp, country=serp_country,
                    vendor_name="Google Images", vendor_domain="google.com",
                )
                for it in result:
                    it["source"] = "serp"
                    all_items.append(it)
                serp_count = len(result)
            else:
                errors.append("SERPAPI_KEY not set")
        except Exception as e:
            errors.append(f"Google: {e}")

    if not all_items and errors:
        return JSONResponse({"error": "; ".join(errors)}, status_code=400)
    if not all_items:
        return JSONResponse(
            {"error": "No images fetched. Check API keys in .env for the selected source."},
            status_code=400,
        )

    return {
        "ok": True, "count": len(all_items),
        "amazon_count": amazon_count, "serp_count": serp_count,
        "items": all_items,
    }


# ── Local vendor upload ────────────────────────────────────────────────

@router.post("/api/add-local-vendor")
async def api_add_local_vendor(
    image:              UploadFile     = File(...),
    title:              str            = Form(...),
    vendor_name:        str            = Form("Local vendor"),
    product_type:       str            = Form("General"),
    product_subtype:    str            = Form("Other"),
    colour:             Optional[str]  = Form(None),
    style:              Optional[str]  = Form(None),
    material:           Optional[str]  = Form(None),
    source_url:         Optional[str]  = Form(None),
    product_dimensions: Optional[str]  = Form(None),
    width:              Optional[str]  = Form(None),
    height:             Optional[str]  = Form(None),
):
    if not image or not image.filename:
        return JSONResponse({"error": "No image file selected"}, status_code=400)
    title = title.strip()
    if not title:
        return JSONResponse({"error": "Missing 'title'"}, status_code=400)

    vendor_name        = (vendor_name        or "Local vendor").strip()
    product_type       = (product_type       or "General").strip()
    product_subtype    = (product_subtype    or "Other").strip()
    colour             = (colour             or "").strip() or None
    style              = (style              or "").strip() or None
    material           = (material           or "").strip() or None
    source_url         = (source_url         or "").strip() or None
    product_dimensions = (product_dimensions or "").strip() or None
    w = int(width)  if width  and str(width).isdigit()  else None
    h = int(height) if height and str(height).isdigit() else None

    asin    = "local_" + uuid.uuid4().hex[:12]
    out_dir = Path(DIR_2D) / asin
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(image.filename).suffix or ".jpg"
    if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    image_path = out_dir / f"image{ext}"

    content = await image.read()
    try:
        image_path.write_bytes(content)
    except Exception as e:
        return JSONResponse({"error": f"Failed to save image: {e}"}, status_code=500)

    image_base64_str = None
    try:
        from modules.image_utils import get_image_dimensions, get_image_base64
        if w is None or h is None:
            dims = get_image_dimensions(str(image_path))
            if dims:
                w, h = dims
        image_base64_str = get_image_base64(str(image_path))
    except Exception:
        pass

    try:
        upsert_item(
            asin=asin, title=title,
            vendor_name=vendor_name, vendor_domain="",
            product_type=product_type, product_subtype=product_subtype,
            image_paths=[str(image_path)], image_path_used=str(image_path),
            image_url=None, source_url=source_url, query=None,
            style=style, colour=colour, material=material,
            width=w, height=h,
            product_dimensions=product_dimensions,
            image_base64=image_base64_str,
            conversion_status="pending",
        )
    except Exception as e:
        return JSONResponse({"error": f"Database error: {e}"}, status_code=500)

    return {"ok": True, "asin": asin, "item": {
        "asin": asin, "title": title,
        "product_type": product_type, "product_subtype": product_subtype,
        "vendor": vendor_name, "source": "local",
    }}


# ── 3D conversion pipeline triggers ───────────────────────────────────

@router.post("/api/convert-selected")
async def api_convert_selected(request: Request):
    try:
        from pipelines.pipeline_3d import run_3d_pipeline
    except ImportError:
        return JSONResponse({"error": "pipeline_3d not available"}, status_code=500)

    data  = await request.json()
    limit = int(data.get("limit", 3))
    try:
        result = run_3d_pipeline(limit=limit)
        return {"ok": True, "results": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/convert-item")
async def api_convert_item(request: Request):
    try:
        from pipelines.pipeline_3d import run_3d_single
    except ImportError:
        return JSONResponse({"error": "pipeline_3d not available"}, status_code=500)

    data  = await request.json()
    asin  = (data.get("asin") or "").strip()
    if not asin:
        return JSONResponse({"error": "Missing asin"}, status_code=400)
    image_index = int(data.get("image_index", 0))

    item = get_item_by_asin(asin)
    if not item:
        return JSONResponse({"error": "Item not found"}, status_code=404)

    paths = item.get("image_paths") or []
    if isinstance(paths, str):
        try:
            paths = json.loads(paths)
        except Exception:
            paths = []
    path = (
        paths[image_index]
        if paths and 0 <= image_index < len(paths)
        else (paths[0] if paths else item.get("image_path_used"))
    )
    if not path or not Path(path).is_file():
        return JSONResponse({"error": "No image file for selected index"}, status_code=400)

    try:
        result = run_3d_single(asin, path)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Static file serving ────────────────────────────────────────────────

@router.get("/api/files/{subpath:path}")
def api_files(subpath: str):
    if ".." in subpath or subpath.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    data_dir = Path(DATA_DIR).resolve()
    path     = (Path(DATA_DIR) / subpath).resolve()
    if not str(path).startswith(str(data_dir)) or path == data_dir:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    mimetype = "model/gltf-binary" if path.suffix.lower() == ".glb" else None
    if path.suffix.lower() == ".usdz":
        mimetype = "model/vnd.usdz+zip"
    return FileResponse(str(path), media_type=mimetype)


# ── Dollhouse (Unity scan: name, scan_json, usdz) ───────────────────────

def _enrich_dollhouse(d: dict) -> dict:
    """Add usdz_url for frontend."""
    if d is None:
        return d
    d = dict(d)
    d["usdz_url"] = _path_to_file_url(d.get("usdz_path"))
    return d


@router.post("/api/dollhouse")
async def api_dollhouse_create(
    name:      str = Form(...),
    scan_json: str = Form(...),
    usdz_file: UploadFile = File(...),
):
    """Create a dollhouse node: name, scan_json string, and uploaded usdz file. Stores file under data/dollhouse/{id}/."""
    if not name or not name.strip():
        return JSONResponse({"error": "Missing 'name'"}, status_code=400)
    name = name.strip()
    if not usdz_file.filename or not usdz_file.filename.lower().endswith(".usdz"):
        return JSONResponse({"error": "Upload a .usdz file"}, status_code=400)

    dollhouse_id = "dh_" + uuid.uuid4().hex[:12]
    out_dir = Path(DIR_DOLLHOUSE) / dollhouse_id
    out_dir.mkdir(parents=True, exist_ok=True)
    usdz_path = out_dir / "model.usdz"

    content = await usdz_file.read()
    try:
        usdz_path.write_bytes(content)
    except Exception as e:
        return JSONResponse({"error": f"Failed to save usdz file: {e}"}, status_code=500)

    try:
        upsert_dollhouse(
            name=name,
            scan_json=scan_json,
            usdz_path=str(usdz_path),
            dollhouse_id=dollhouse_id,
        )
    except Exception as e:
        return JSONResponse({"error": f"Database error: {e}"}, status_code=500)

    item = get_dollhouse(dollhouse_id)
    return {"ok": True, "dollhouse_id": dollhouse_id, "item": _enrich_dollhouse(item)}


@router.get("/api/dollhouse")
def api_dollhouse_list(limit: int = 100, offset: int = 0):
    """List all dollhouse nodes."""
    try:
        items = list_dollhouses(limit=min(limit, 200), offset=offset)
        return {"items": [_enrich_dollhouse(d) for d in items]}
    except Exception as e:
        return JSONResponse({
            "items": [],
            "warning": "Catalog database unavailable.",
            "detail": str(e),
        }, status_code=200)


@router.get("/api/dollhouse/{dollhouse_id}")
def api_dollhouse_get(dollhouse_id: str):
    """Get a single dollhouse by id."""
    item = get_dollhouse(dollhouse_id)
    if not item:
        raise HTTPException(status_code=404, detail="Dollhouse not found")
    return _enrich_dollhouse(item)


def _usdz_to_glb_bytes_aspose(usdz_path: Path) -> bytes:
    """Full-scene USDZ→GLB via Aspose.3D (preserves hierarchy, transforms, materials)."""
    import io
    import aspose.threed as a3d
    scene = a3d.Scene.from_file(str(usdz_path))
    buf = io.BytesIO()
    scene.save(buf, a3d.FileFormat.GLB_BINARY)
    return buf.getvalue()


def _usdz_to_glb_bytes_pxr(usdz_path: Path) -> bytes:
    """
    Fallback: extract meshes from USD via pxr + trimesh. Does NOT apply transforms,
    so complex scenes can appear as a single flattened mesh ("plank").
    """
    import tempfile
    import zipfile

    import numpy as np
    import trimesh
    from pxr import Usd, UsdGeom

    usdz_path = Path(usdz_path).resolve()
    if not usdz_path.is_file():
        raise FileNotFoundError(f"USDZ file not found: {usdz_path}")
    if not zipfile.is_zipfile(usdz_path):
        raise ValueError("USDZ is not a zip file")

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(usdz_path, "r") as z:
            z.extractall(tmp)
        tmp_path = Path(tmp)
        usd_file = None
        for f in tmp_path.rglob("*"):
            if f.suffix.lower() in (".usdc", ".usda", ".usd"):
                usd_file = f
                break
        if usd_file is None:
            raise ValueError("No .usdc/.usda/.usd file found inside USDZ")

        stage = Usd.Stage.Open(str(usd_file))
        if stage is None:
            raise ValueError("Usd.Stage.Open failed")

        meshes = []
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(prim)
            points_attr = mesh.GetPointsAttr()
            face_indices_attr = mesh.GetFaceVertexIndicesAttr()
            face_counts_attr = mesh.GetFaceVertexCountsAttr()
            if not points_attr or not face_indices_attr or not face_counts_attr:
                continue
            points = np.array(points_attr.Get(), dtype=np.float64)
            face_indices = np.array(face_indices_attr.Get(), dtype=np.int32)
            face_counts = np.array(face_counts_attr.Get(), dtype=np.int32)
            if len(points) == 0 or len(face_indices) == 0:
                continue

            triangles = []
            offset = 0
            for count in face_counts:
                if count < 3:
                    offset += count
                    continue
                idx = face_indices[offset : offset + count]
                offset += count
                if count == 3:
                    triangles.append(idx)
                else:
                    for i in range(1, count - 1):
                        triangles.append([idx[0], idx[i], idx[i + 1]])
            if not triangles:
                continue
            faces = np.array(triangles, dtype=np.int32)
            tri_mesh = trimesh.Trimesh(vertices=points, faces=faces)
            meshes.append(tri_mesh)

        if not meshes:
            raise ValueError("No UsdGeom.Mesh prims found in USD stage")

        if len(meshes) == 1:
            scene = meshes[0]
        else:
            scene = trimesh.Scene()
            for m in meshes:
                scene.add_geometry(m)
        try:
            glb_bytes = trimesh.exchange.gltf.export_glb(scene)
        except Exception as e:
            logger.info("export_glb failed, using export: %s", e)
            glb_bytes = scene.export(file_type="glb")
        if isinstance(glb_bytes, str):
            glb_bytes = glb_bytes.encode("utf-8")
        return glb_bytes


def _usdz_to_glb_bytes(usdz_path: Path) -> bytes:
    """
    Convert USDZ to GLB for browser preview.
    Prefer Aspose.3D (full-scene, correct layout). Fallback: pxr+trimesh (may look like a single plank).
    Public API alternative: Sirv (https://api.sirv.com/v2/files/3d/model2GLB) – upload file to Sirv, then POST to convert.
    """
    usdz_path = Path(usdz_path).resolve()
    if not usdz_path.is_file():
        raise FileNotFoundError(f"USDZ file not found: {usdz_path}")

    try:
        return _usdz_to_glb_bytes_aspose(usdz_path)
    except ImportError:
        logger.info("aspose.threed not installed; use pxr+trimesh fallback (install aspose-3d for full-scene preview)")
    except Exception as e:
        logger.warning("Aspose.3D conversion failed: %s; falling back to pxr+trimesh", e)

    return _usdz_to_glb_bytes_pxr(usdz_path)


@router.get("/api/dollhouse/{dollhouse_id}/preview")
def api_dollhouse_preview(dollhouse_id: str):
    """
    Load the dollhouse USDZ with trimesh, export to GLB, and return for browser preview.
    model-viewer supports GLB but not USDZ in most browsers, so we convert server-side.
    """
    item = get_dollhouse(dollhouse_id)
    if not item:
        raise HTTPException(status_code=404, detail="Dollhouse not found")
    usdz_path = item.get("usdz_path")
    if not usdz_path:
        raise HTTPException(status_code=404, detail="USDZ file path not set")
    path = Path(usdz_path).resolve()
    if not path.is_file():
        # Fallback: canonical location under data/dollhouse/{id}/model.usdz
        alt = Path(DIR_DOLLHOUSE) / dollhouse_id / "model.usdz"
        if alt.is_file():
            path = alt.resolve()
        else:
            raise HTTPException(status_code=404, detail=f"USDZ file not found: {path}")
    try:
        glb_bytes = _usdz_to_glb_bytes(path)
        return Response(
            content=glb_bytes,
            media_type="model/gltf-binary",
            headers={"Content-Disposition": "inline; filename=preview.glb"},
        )
    except Exception as e:
        logger.exception("USDZ preview failed for %s: %s", dollhouse_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert USDZ to GLB for preview: {e}",
        )
