"""
modules/image_analyser.py
--------------------------
Analyses uploaded room images via the Vanilla Prompt API (Template 15).
Each image is sent individually; results are aggregated by the caller.

Replaces the old direct Gemini Vision approach.
"""

from flask import current_app
from modules.gemini_ai import analyse_single_image_vanilla


def analyse_images(image_files: list) -> list[dict]:
    """
    Analyse a list of werkzeug FileStorage objects using Template 15.

    Args:
        image_files: List of werkzeug FileStorage objects from request.files

    Returns:
        List of analysis dicts — one per image.
        Each dict has: filename, styles, dominant_colours, materials,
                       mood_tags, spatial_density, confidence, [error]
    """
    results = []

    for f in image_files:
        try:
            img_bytes = f.read()
            f.seek(0)
            mimetype  = f.content_type or "image/jpeg"

            result = analyse_single_image_vanilla(f.filename, img_bytes, mimetype)
            results.append(result)

            current_app.logger.info(
                f"Analysed {f.filename}: styles={result.get('styles')} "
                f"confidence={result.get('confidence')}"
            )
        except Exception as e:
            current_app.logger.warning(f"Failed to analyse {f.filename}: {e}")
            results.append({
                "filename": f.filename, "error": str(e),
                "styles": [], "dominant_colours": [], "materials": [],
                "mood_tags": [], "spatial_density": "unknown", "confidence": 0.0,
            })

    return results
