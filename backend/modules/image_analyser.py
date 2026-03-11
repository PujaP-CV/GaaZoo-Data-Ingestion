"""
modules/image_analyser.py
--------------------------
Analyses uploaded room images via the Vanilla Prompt API (Template 15).
Each image is sent individually; results are aggregated by the caller.

Accepts a list of ImageData objects (see dataclass below) — framework-agnostic,
works with both Flask FileStorage and FastAPI UploadFile (bytes already read).
"""

import logging
from dataclasses import dataclass

from modules.gemini_ai import analyse_single_image_vanilla

logger = logging.getLogger(__name__)


@dataclass
class ImageData:
    """Lightweight container for an uploaded image — replaces werkzeug FileStorage."""
    filename: str
    content: bytes
    content_type: str


def analyse_images(image_files: list) -> list[dict]:
    """
    Analyse a list of ImageData objects using Template 15.

    Args:
        image_files: List of ImageData (or any object with .filename, .content, .content_type)

    Returns:
        List of analysis dicts — one per image.
        Each dict has: filename, styles, dominant_colours, materials,
                       mood_tags, spatial_density, confidence, [error]
    """
    results = []

    for f in image_files:
        try:
            img_bytes = f.content
            mimetype  = f.content_type or "image/jpeg"

            result = analyse_single_image_vanilla(f.filename, img_bytes, mimetype)
            results.append(result)

            logger.info(
                f"Analysed {f.filename}: styles={result.get('styles')} "
                f"confidence={result.get('confidence')}"
            )
        except Exception as e:
            logger.warning(f"Failed to analyse {f.filename}: {e}")
            results.append({
                "filename": f.filename, "error": str(e),
                "styles": [], "dominant_colours": [], "materials": [],
                "mood_tags": [], "spatial_density": "unknown", "confidence": 0.0,
            })

    return results
