"""Meshy image-to-3D: create task, poll until done, return GLB URL."""
import time
import base64
import requests
from pathlib import Path
from typing import Optional, Tuple

from config import MESHY_API_KEY, MESHY_BASE


def _task_from_status_response(status_data: dict) -> dict:
    if "result" in status_data and isinstance(status_data["result"], dict):
        return status_data["result"]
    return status_data


def image_path_to_data_uri(path: str) -> str:
    """Read local image file and return data URI."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    raw = p.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    suffix = p.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{b64}"


def convert_image_to_3d(
    image_path: str,
    timeout_seconds: int = 300,
    poll_interval: int = 2,
) -> Tuple[Optional[str], Optional[str], str]:
    """
    Convert a local image to 3D via Meshy. Returns (glb_url, task_id, status).
    status is 'succeeded', 'failed', or 'timeout'.
    """
    if not MESHY_API_KEY:
        raise ValueError("MESHY_API_KEY is not set in .env")
    image_url = image_path_to_data_uri(image_path)
    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"image_url": image_url}
    response = requests.post(MESHY_BASE, headers=headers, json=payload, timeout=30)
    data = response.json()
    task_id = data.get("result")
    if not task_id:
        raise RuntimeError(data.get("message", data.get("error", "Failed to create task")))
    status_url = f"{MESHY_BASE}/{task_id}"
    started = time.time()
    while time.time() - started < timeout_seconds:
        try:
            status_response = requests.get(status_url, headers=headers, timeout=30)
            status_data = status_response.json()
        except Exception:
            time.sleep(poll_interval)
            continue
        task = _task_from_status_response(status_data)
        status = task.get("status")
        if status == "SUCCEEDED":
            model_urls = task.get("model_urls") or {}
            glb_url = model_urls.get("glb")
            return (glb_url, task_id, "succeeded")
        if status == "FAILED":
            return (None, task_id, "failed")
        time.sleep(poll_interval)
    return (None, task_id, "timeout")
