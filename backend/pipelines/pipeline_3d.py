"""3D conversion pipeline: select catalog items → convert via Meshy → save GLB → update catalog."""

import requests
from pathlib import Path
from typing import List, Dict, Any

from config import MESHY_API_KEY, DIR_3D
from modules.catalog_db import (
    init_db, get_items_for_conversion, update_conversion_result, update_conversion_failed
)
from modules.meshy_client import convert_image_to_3d


def _download_glb(url: str, path: Path) -> bool:
    try:
        r = requests.get(url, stream=True, timeout=120)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return True
    except Exception:
        return False


def run_3d_pipeline(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Select items that have 2D but no 3D, convert each via Meshy,
    save GLB to data/3d/<asin>.glb, update catalog.
    Returns list of { asin, status, glb_path? }.
    """
    if not MESHY_API_KEY:
        raise ValueError("MESHY_API_KEY is not set in .env")
    init_db()
    items   = get_items_for_conversion(limit=limit)
    results = []
    for it in items:
        asin       = it["asin"]
        image_path = it.get("image_path_used")
        if not image_path or not Path(image_path).is_file():
            update_conversion_failed(asin)
            results.append({"asin": asin, "status": "failed", "error": "No image file"})
            continue
        try:
            glb_url, task_id, status = convert_image_to_3d(image_path)
        except Exception as e:
            update_conversion_failed(asin)
            results.append({"asin": asin, "status": "failed", "error": str(e)})
            continue
        if status == "succeeded" and glb_url:
            glb_path = DIR_3D / f"{asin}.glb"
            if _download_glb(glb_url, glb_path):
                update_conversion_result(asin, glb_path=str(glb_path), conversion_status="succeeded", meshy_task_id=task_id)
                results.append({"asin": asin, "status": "succeeded", "glb_path": str(glb_path)})
            else:
                update_conversion_failed(asin, meshy_task_id=task_id)
                results.append({"asin": asin, "status": "failed", "error": "Could not download GLB"})
        else:
            update_conversion_failed(asin, meshy_task_id=task_id)
            results.append({"asin": asin, "status": status, "error": "Conversion failed or timed out"})
    return results


def run_3d_single(asin: str, image_path: str) -> Dict[str, Any]:
    """Convert a single catalog item's image to 3D."""
    if not MESHY_API_KEY:
        raise ValueError("MESHY_API_KEY is not set in .env")
    init_db()
    if not image_path or not Path(image_path).is_file():
        update_conversion_failed(asin)
        return {"asin": asin, "status": "failed", "error": "Image file not found"}
    try:
        glb_url, task_id, status = convert_image_to_3d(image_path)
    except Exception as e:
        update_conversion_failed(asin)
        return {"asin": asin, "status": "failed", "error": str(e)}
    if status == "succeeded" and glb_url:
        glb_path = DIR_3D / f"{asin}.glb"
        if _download_glb(glb_url, glb_path):
            update_conversion_result(asin, glb_path=str(glb_path), conversion_status="succeeded", meshy_task_id=task_id)
            return {"asin": asin, "status": "succeeded", "glb_path": str(glb_path)}
        update_conversion_failed(asin, meshy_task_id=task_id)
        return {"asin": asin, "status": "failed", "error": "Could not download GLB"}
    update_conversion_failed(asin, meshy_task_id=task_id)
    return {"asin": asin, "status": status, "error": "Conversion failed or timed out"}
