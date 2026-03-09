"""
Helpers for image metadata: read dimensions and base64 from local files.
Uses Pillow if available so we always have width/height for downloaded images.
"""
from pathlib import Path
from typing import Optional, Tuple
import base64


def get_image_dimensions(path: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """
    Return (width, height) in pixels for an image file, or (None, None) if unreadable.
    Requires Pillow: pip install Pillow
    """
    if not path:
        return None, None
    p = Path(path)
    if not p.is_file():
        return None, None
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.width, im.height
    except Exception:
        return None, None


def get_image_base64(path: Optional[str]) -> Optional[str]:
    """
    Return base64-encoded contents of an image file, or None if unreadable.
    """
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = p.read_bytes()
        return base64.b64encode(data).decode("utf-8")
    except Exception:
        return None
