"""
Catalog routes — Image Pipeline module.
Handles: catalog CRUD, image fetching (Amazon / Google), local vendor upload,
         3D conversion pipeline triggers, and static file serving.
"""

import json
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, send_from_directory

from config import DATA_DIR, DIR_2D, DIR_3D
from modules.catalog_db import init_db, list_items, get_item_by_asin, upsert_item

catalog_bp = Blueprint("catalog", __name__)


# ── Helpers ────────────────────────────────────────────────────────────

def _path_to_file_url(path):
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

@catalog_bp.route("/api/catalog", methods=["GET"])
def api_catalog():
    status = request.args.get("status")
    limit  = min(int(request.args.get("limit",  100)), 200)
    offset = int(request.args.get("offset", 0))
    items  = list_items(conversion_status=status, limit=limit, offset=offset)
    return jsonify({"items": [_enrich(it) for it in items]})


@catalog_bp.route("/api/catalog/<asin>", methods=["GET"])
def api_catalog_item(asin):
    item = get_item_by_asin(asin)
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_enrich(item))


@catalog_bp.route("/api/catalog/<asin>", methods=["DELETE"])
def api_catalog_delete(asin):
    from modules.catalog_db import delete_item
    delete_files = request.args.get("files", "1").strip().lower() not in ("0", "false", "no")
    if delete_item(asin, delete_files=delete_files):
        return jsonify({"ok": True, "asin": asin})
    return jsonify({"error": "Not found"}), 404


# ── Fetch images (Amazon / Google SERP → Neo4j) ────────────────────────

@catalog_bp.route("/api/fetch-images", methods=["POST"])
def api_fetch_images():
    data   = request.get_json() or {}
    source = (data.get("source") or "").strip().lower() or "amazon"
    if source not in ("amazon", "google"):
        return jsonify({"error": "source must be 'amazon' or 'google'"}), 400

    query = data.get("query") or request.form.get("query")
    if not query:
        return jsonify({"error": "Missing 'query'"}), 400

    country    = (data.get("country") or "IN").strip() or "IN"
    max_amazon = int(data.get("max_amazon", 5))
    num_serp   = int(data.get("num_serp", 10))
    max_images_per_product = int(data.get("max_images_per_product", 3))
    serp_country = country.lower() if len(country) == 2 else "in"

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
        except (ValueError, Exception) as e:
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
        except (ValueError, Exception) as e:
            errors.append(f"Google: {e}")

    if not all_items and errors:
        return jsonify({"error": "; ".join(errors)}), 400
    if not all_items:
        return jsonify({"error": "No images fetched. Check API keys in .env for the selected source."}), 400

    return jsonify({
        "ok": True, "count": len(all_items),
        "amazon_count": amazon_count, "serp_count": serp_count,
        "items": all_items,
    })


# ── Local vendor upload ────────────────────────────────────────────────

@catalog_bp.route("/api/add-local-vendor", methods=["POST"])
def api_add_local_vendor():
    if not request.files or "image" not in request.files:
        return jsonify({"error": "Missing 'image' file"}), 400

    file = request.files["image"]
    if not file or not file.filename:
        return jsonify({"error": "No image file selected"}), 400

    title = (request.form.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Missing 'title'"}), 400

    vendor_name        = (request.form.get("vendor_name")        or "Local vendor").strip()
    product_type       = (request.form.get("product_type")       or "General").strip()
    product_subtype    = (request.form.get("product_subtype")    or "Other").strip()
    colour             = (request.form.get("colour")             or "").strip() or None
    style              = (request.form.get("style")              or "").strip() or None
    material           = (request.form.get("material")           or "").strip() or None
    source_url         = (request.form.get("source_url")         or "").strip() or None
    product_dimensions = (request.form.get("product_dimensions") or "").strip() or None

    w_str = request.form.get("width")
    h_str = request.form.get("height")
    width  = int(w_str) if w_str and str(w_str).isdigit() else None
    height = int(h_str) if h_str and str(h_str).isdigit() else None

    asin    = "local_" + uuid.uuid4().hex[:12]
    out_dir = Path(DIR_2D) / asin
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix or ".jpg"
    if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    image_path = out_dir / f"image{ext}"
    try:
        file.save(str(image_path))
    except Exception as e:
        return jsonify({"error": f"Failed to save image: {e}"}), 500

    image_base64_str = None
    try:
        from modules.image_utils import get_image_dimensions, get_image_base64
        if width is None or height is None:
            w, h = get_image_dimensions(str(image_path))
            if w and h:
                width, height = w, h
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
            width=width, height=height,
            product_dimensions=product_dimensions,
            image_base64=image_base64_str,
            conversion_status="pending",
        )
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500

    return jsonify({"ok": True, "asin": asin, "item": {
        "asin": asin, "title": title,
        "product_type": product_type, "product_subtype": product_subtype,
        "vendor": vendor_name, "source": "local",
    }})


# ── 3D conversion pipeline triggers ───────────────────────────────────

@catalog_bp.route("/api/convert-selected", methods=["POST"])
def api_convert_selected():
    try:
        from pipelines.pipeline_3d import run_3d_pipeline
    except ImportError:
        return jsonify({"error": "pipeline_3d not available"}), 500

    data  = request.get_json() or {}
    limit = int(data.get("limit") or request.form.get("limit") or 3)
    try:
        result = run_3d_pipeline(limit=limit)
        return jsonify({"ok": True, "results": result})
    except (ValueError, Exception) as e:
        return jsonify({"error": str(e)}), 500


@catalog_bp.route("/api/convert-item", methods=["POST"])
def api_convert_item():
    try:
        from pipelines.pipeline_3d import run_3d_single
    except ImportError:
        return jsonify({"error": "pipeline_3d not available"}), 500

    data  = request.get_json() or {}
    asin  = (data.get("asin") or "").strip()
    if not asin:
        return jsonify({"error": "Missing asin"}), 400
    image_index = int(data.get("image_index", 0))

    item = get_item_by_asin(asin)
    if not item:
        return jsonify({"error": "Item not found"}), 404

    paths = item.get("image_paths") or []
    if isinstance(paths, str):
        try:
            paths = json.loads(paths)
        except Exception:
            paths = []
    path = paths[image_index] if paths and 0 <= image_index < len(paths) else (paths[0] if paths else item.get("image_path_used"))
    if not path or not Path(path).is_file():
        return jsonify({"error": "No image file for selected index"}), 400

    try:
        result = run_3d_single(asin, path)
        return jsonify(result)
    except (ValueError, Exception) as e:
        return jsonify({"error": str(e)}), 500


# ── Static file serving ────────────────────────────────────────────────

@catalog_bp.route("/api/files/<path:subpath>")
def api_files(subpath):
    if ".." in subpath or subpath.startswith("/"):
        return jsonify({"error": "Invalid path"}), 400
    data_dir = Path(DATA_DIR).resolve()
    path     = (Path(DATA_DIR) / subpath).resolve()
    if not str(path).startswith(str(data_dir)) or path == data_dir:
        return jsonify({"error": "Invalid path"}), 400
    if not path.is_file():
        return jsonify({"error": "Not found"}), 404
    mimetype = "model/gltf-binary" if path.suffix.lower() == ".glb" else None
    return send_from_directory(str(path.parent), path.name, mimetype=mimetype)
