from flask import Flask, request, jsonify, send_from_directory, Response
import json
import requests
import time
import base64
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from config import ROOT, DATA_DIR, DIR_2D, DIR_3D, MESHY_API_KEY, MESHY_BASE
from catalog_db import init_db, list_items, get_item_by_asin, upsert_item

app = Flask(__name__, static_folder=None)
BASE = MESHY_BASE


@app.before_request
def _ensure_db():
    # Skip DB for routes that don't need Neo4j (homepage, browser probes)
    if request.path == "/" or request.path.startswith("/.well-known"):
        return
    try:
        init_db()
    except Exception as e:
        from neo4j.exceptions import AuthError, ServiceUnavailable
        if isinstance(e, AuthError):
            return jsonify({
                "error": "Neo4j authentication failed. Check NEO4J_USER and NEO4J_PASSWORD in .env match your Neo4j server (e.g. Neo4j Browser or Aura).",
            }), 503
        if isinstance(e, ServiceUnavailable):
            return jsonify({
                "error": "Neo4j is not reachable. Start Neo4j and ensure NEO4J_URI (e.g. bolt://localhost:7687) is correct.",
            }), 503
        raise


@app.route("/")
def index():
    return send_from_directory(str(ROOT), "index.html")


# ── Meshy GLB proxy ───────────────────────────────────────────────────

@app.route("/proxy-glb")
def proxy_glb():
    """Stream GLB from Meshy URL so the 3D viewer can load it (avoids CORS)."""
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


def _task_from_status_response(status_data):
    if "result" in status_data and isinstance(status_data["result"], dict):
        return status_data["result"]
    return status_data


# ── 3D generation (single image upload) ──────────────────────────────

@app.route('/generate-3d', methods=['POST'])
def generate_3d():
    if not MESHY_API_KEY:
        return jsonify({"error": "MESHY_API_KEY is not set. Add it to your .env file."}), 500

    image = request.files.get('image')
    if not image:
        return jsonify({"error": "No image provided"}), 400

    # Optional real-world dimensions for post-generation rescaling
    obj_width = request.form.get("obj_width", "").strip() or None
    obj_height = request.form.get("obj_height", "").strip() or None
    obj_depth = request.form.get("obj_depth", "").strip() or None
    obj_unit = request.form.get("obj_unit", "cm").strip() or "cm"
    has_dims = any([obj_width, obj_height, obj_depth])

    raw  = image.read()
    b64  = base64.b64encode(raw).decode("utf-8")
    mime = image.content_type or "image/jpeg"
    image_url = f"data:{mime};base64,{b64}"

    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {"image_url": image_url}

    response = requests.post(BASE, headers=headers, json=payload)
    data = response.json()

    task_id = data.get("result")
    if not task_id:
        msg  = data.get("message", data.get("error", "Failed to create task"))
        code = 400 if response.status_code < 500 else 502
        return jsonify({"error": str(msg)}), code

    status_url      = f"{BASE}/{task_id}"
    timeout_seconds = 300
    started         = time.time()
    request_timeout = 30
    poll_headers    = {**headers, "Connection": "close"}
    max_retries     = 3

    while True:
        if time.time() - started > timeout_seconds:
            return jsonify({"error": "Conversion timed out after 5 minutes. Try again or use a simpler image."}), 504

        last_error = None
        for attempt in range(max_retries):
            try:
                status_response = requests.get(
                    status_url, headers=poll_headers, timeout=request_timeout
                )
                break
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(2)
                continue
        else:
            return (
                jsonify(
                    {
                        "error": "Connection to 3D service was reset. Please try again in a moment."
                    }
                ),
                502,
            )

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
                return jsonify({"error": "No model URL in response", "task": task, "status_data": status_data}), 500

            scaled_model_url = None
            scale_info       = None

            if has_dims:
                try:
                    from model_scaler import scale_model
                    glb_id      = uuid.uuid4().hex[:12]
                    orig_path   = Path(str(DIR_3D)) / f"upload_{glb_id}_orig.glb"
                    scaled_path = Path(str(DIR_3D)) / f"upload_{glb_id}_scaled.glb"

                    glb_r = requests.get(model_url, timeout=60)
                    glb_r.raise_for_status()
                    orig_path.parent.mkdir(parents=True, exist_ok=True)
                    orig_path.write_bytes(glb_r.content)

                    scale_info = scale_model(
                        str(orig_path),
                        str(scaled_path),
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
                "model_url":               model_url,
                "scaled_model_url":        scaled_model_url,
                "scale_info":              scale_info,
                "task":                    task,
                "status_data":             status_data,
                "status_response_headers": dict(status_response.headers),
            })
        if status == "FAILED":
            err = (task.get("task_error") or {}).get("message", "Generation failed")
            return jsonify({
                "error":                   err,
                "task":                    task,
                "status_data":             status_data,
                "status_response_headers": dict(status_response.headers),
            }), 400

        if status not in ("PENDING", "IN_PROGRESS", None):
            return jsonify({"error": f"Unexpected status: {status}"}), 500

        time.sleep(2)


# ── Helpers ───────────────────────────────────────────────────────────

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
    # Keep original remote URL (Amazon/Google etc.) for Source link; use local serve URL for display
    item["image_url_original"] = item.get("image_url")  # remote URL, e.g. https://m.media-amazon.com/...
    item["image_url"] = _path_to_file_url(item.get("image_path_used")) or item.get("image_url")
    item["glb_url"]   = _path_to_file_url(item.get("glb_path"))
    # Gallery: list of serveable URLs from image_paths (Amazon: multiple; SERP: single)
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


# ── Catalog CRUD ──────────────────────────────────────────────────────

@app.route("/api/catalog", methods=["GET"])
def api_catalog():
    """List catalog items. Query: status=, limit=, offset="""
    status = request.args.get("status")
    limit  = min(int(request.args.get("limit",  100)), 200)
    offset = int(request.args.get("offset", 0))
    items  = list_items(conversion_status=status, limit=limit, offset=offset)
    return jsonify({"items": [_enrich(it) for it in items]})


@app.route("/api/catalog/<asin>", methods=["GET"])
def api_catalog_item(asin):
    """Get one catalog item by ASIN / image_id."""
    item = get_item_by_asin(asin)
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_enrich(item))


@app.route("/api/catalog/<asin>", methods=["DELETE"])
def api_catalog_delete(asin):
    """Remove an item. Query ?files=0 to keep local files on disk."""
    from catalog_db import delete_item
    delete_files = request.args.get("files", "1").strip().lower() not in ("0", "false", "no")
    if delete_item(asin, delete_files=delete_files):
        return jsonify({"ok": True, "asin": asin})
    return jsonify({"error": "Not found"}), 404


# ── Unified image fetch (Amazon + Google SERP → Neo4j) ─────────────────

@app.route("/api/fetch-images", methods=["POST"])
def api_fetch_images():
    """
    Fetch images from Amazon and/or Google SERP for a query, store in Neo4j.

    Body (JSON):
      {
        "source":         "amazon" | "google",  // which source to use (required)
        "query":          "office chair ...",   // required
        "max_amazon":     5,   // for source=amazon
        "num_serp":       10,  // for source=google
        "country":        "IN"
      }
    Returns: { "ok": true, "count": N, "amazon_count": A, "serp_count": S, "items": [...] }
    """
    data = request.get_json() or {}
    source = (data.get("source") or "").strip().lower() or "amazon"
    if source not in ("amazon", "google"):
        return jsonify({"error": "source must be 'amazon' or 'google'"}), 400

    query = data.get("query") or request.form.get("query")
    if not query:
        return jsonify({"error": "Missing 'query'"}), 400

    country = (data.get("country") or "IN").strip() or "IN"
    max_amazon = int(data.get("max_amazon", 5))
    num_serp = int(data.get("num_serp", 10))
    max_images_per_product = int(data.get("max_images_per_product", 3))
    serp_country = country.lower() if len(country) == 2 else "in"

    all_items = []
    amazon_count = 0
    serp_count = 0
    errors = []

    if source == "amazon":
        try:
            from pipeline_amazon import run_amazon_pipeline
            from config import RAPIDAPI_KEY
            if RAPIDAPI_KEY:
                result = run_amazon_pipeline(
                    query=query,
                    country=country,
                    max_products=max_amazon,
                    max_images_per_product=max_images_per_product,
                )
                for it in result:
                    it["source"] = "amazon"
                    all_items.append(it)
                amazon_count = len(result)
            else:
                errors.append("RAPIDAPI_KEY not set")
        except ImportError:
            pass
        except ValueError as e:
            errors.append(f"Amazon: {e}")
        except Exception as e:
            errors.append(f"Amazon: {e}")

    if source == "google":
        try:
            from pipeline_serp import run_serp_pipeline
            from config import SERPAPI_KEY
            if SERPAPI_KEY:
                result = run_serp_pipeline(
                    query=query,
                    num=num_serp,
                    country=serp_country,
                    vendor_name="Google Images",
                    vendor_domain="google.com",
                )
                for it in result:
                    it["source"] = "serp"
                    all_items.append(it)
                serp_count = len(result)
            else:
                errors.append("SERPAPI_KEY not set")
        except ImportError:
            pass
        except ValueError as e:
            errors.append(f"Google: {e}")
        except Exception as e:
            errors.append(f"Google: {e}")

    if not all_items and errors:
        return jsonify({"error": "; ".join(errors)}), 400
    if not all_items:
        return jsonify({
            "error": "No images fetched. Check API keys in .env for the selected source.",
        }), 400

    return jsonify({
        "ok": True,
        "count": len(all_items),
        "amazon_count": amazon_count,
        "serp_count": serp_count,
        "items": all_items,
    })


# ── Local vendor (manual add with image upload → Neo4j) ─────────────────

@app.route("/api/add-local-vendor", methods=["POST"])
def api_add_local_vendor():
    """
    Add a single product from a local vendor: form fields + image file.
    Saves image to data/2d/local_<id>/, generates base64, upserts to Neo4j.
    """
    if not request.files or "image" not in request.files:
        return jsonify({"error": "Missing 'image' file"}), 400

    file = request.files["image"]
    if not file or not file.filename:
        return jsonify({"error": "No image file selected"}), 400

    title = (request.form.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Missing 'title'"}), 400

    vendor_name = (request.form.get("vendor_name") or "Local vendor").strip()
    product_type = (request.form.get("product_type") or "General").strip()
    product_subtype = (request.form.get("product_subtype") or "Other").strip()
    colour = (request.form.get("colour") or "").strip() or None
    style = (request.form.get("style") or "").strip() or None
    material = (request.form.get("material") or "").strip() or None
    source_url = (request.form.get("source_url") or "").strip() or None
    width = request.form.get("width")
    height = request.form.get("height")
    width = int(width) if width and str(width).isdigit() else None
    height = int(height) if height and str(height).isdigit() else None
    product_dimensions = (request.form.get("product_dimensions") or "").strip() or None

    asin = "local_" + uuid.uuid4().hex[:12]
    safe_id = asin.replace("/", "_")
    out_dir = Path(DIR_2D) / safe_id
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix or ".jpg"
    if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    image_path = out_dir / f"image{ext}"
    try:
        file.save(str(image_path))
    except Exception as e:
        return jsonify({"error": f"Failed to save image: {e}"}), 500

    path_str = str(image_path)
    try:
        from image_utils import get_image_dimensions, get_image_base64
        if width is None or height is None:
            w, h = get_image_dimensions(path_str)
            if w and h:
                width, height = w, h
        image_base64_str = get_image_base64(path_str)
    except Exception as e:
        image_base64_str = None

    try:
        upsert_item(
            asin=asin,
            title=title,
            vendor_name=vendor_name,
            vendor_domain="",
            product_type=product_type,
            product_subtype=product_subtype,
            image_paths=[path_str],
            image_path_used=path_str,
            image_url=None,
            source_url=source_url,
            query=None,
            style=style,
            colour=colour,
            material=material,
            width=width,
            height=height,
            product_dimensions=product_dimensions,
            image_base64=image_base64_str,
            conversion_status="pending",
        )
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500

    item = {
        "asin": asin,
        "title": title,
        "product_type": product_type,
        "product_subtype": product_subtype,
        "vendor": vendor_name,
        "source": "local",
    }
    return jsonify({"ok": True, "asin": asin, "item": item})


# ── 3D conversion pipeline ────────────────────────────────────────────

@app.route("/api/convert-selected", methods=["POST"])
def api_convert_selected():
    """Convert pending catalog items to 3D via Meshy. Body: { "limit": 3 }"""
    try:
        from pipeline_3d import run_3d_pipeline
    except ImportError:
        return jsonify({"error": "pipeline_3d not available"}), 500

    data  = request.get_json() or {}
    limit = int(data.get("limit") or request.form.get("limit") or 3)

    try:
        result = run_3d_pipeline(limit=limit)
        return jsonify({"ok": True, "results": result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/convert-item", methods=["POST"])
def api_convert_item():
    """Convert one catalog item to 3D using the user-selected image. Body: { "asin": "...", "image_index": 0 }"""
    try:
        from pipeline_3d import run_3d_single
    except ImportError:
        return jsonify({"error": "pipeline_3d not available"}), 500

    data = request.get_json() or {}
    asin = (data.get("asin") or "").strip()
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
    if not paths:
        path = item.get("image_path_used")
    else:
        path = paths[image_index] if 0 <= image_index < len(paths) else paths[0]
    if not path or not Path(path).is_file():
        return jsonify({"error": "No image file for selected index"}), 400

    try:
        result = run_3d_single(asin, path)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Static file serving ───────────────────────────────────────────────

@app.route("/api/files/<path:subpath>")
def api_files(subpath):
    """Serve local files under data/ (2D images and 3D GLBs)."""
    if ".." in subpath or subpath.startswith("/"):
        return jsonify({"error": "Invalid path"}), 400
    data_dir = Path(DATA_DIR).resolve()
    path     = (Path(DATA_DIR) / subpath).resolve()
    if not str(path).startswith(str(data_dir)) or path == data_dir:
        return jsonify({"error": "Invalid path"}), 400
    if not path.is_file():
        return jsonify({"error": "Not found"}), 404
    mimetype = None
    if path.suffix.lower() == ".glb":
        mimetype = "model/gltf-binary"
    return send_from_directory(str(path.parent), path.name, mimetype=mimetype)


if __name__ == "__main__":
    app.run(port=5000)
