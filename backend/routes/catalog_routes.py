"""
Catalog routes — Image Pipeline module.
Handles: catalog CRUD, image fetching (Amazon / Google), local vendor upload,
         3D conversion pipeline triggers, and static file serving.
"""

import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from config import DATA_DIR, DIR_2D, DIR_3D
from modules.catalog_db import init_db, list_items, get_item_by_asin, upsert_item

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
    try:
        items = list_items(conversion_status=status, limit=limit, offset=offset)
        return {"items": [_enrich(it) for it in items]}
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
    return FileResponse(str(path), media_type=mimetype)
