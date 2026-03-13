"""
Shapely Layout Demo — API routes.

Accepts JSON upload (OpenCV-style layout with object coordinates and room boundary),
validates against markdown-defined rules using Shapely, returns raw violations
and human-readable explanations.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from modules.shapely_blueprint import layout_to_blueprint_png
from modules.shapely_nudge import nudge_overlaps, space_evaluation
from modules.shapely_rule_engine import evaluate_rules, geometries_to_layout, get_shapely_geometry_output, layout_json_to_geometries
from modules.shapely_response_formatter import RESPONSE_GUIDE, format_response, _build_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shapely", tags=["Shapely Layout Demo"])

EXAMPLE_LAYOUT_PATH = Path(__file__).resolve().parent.parent / "data" / "shapely_example_layout.json"


@router.post("/validate-layout")
async def validate_layout(file: UploadFile = File(..., description="JSON file with OpenCV object detection coordinates")):
    """
    Upload a JSON file containing layout data (objects + room boundary with coordinates).
    Returns validation result with raw violations and human-readable explanations.
    """
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(400, "Please upload a JSON file.")

    try:
        content = await file.read()
        layout = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(400, str(e))

    try:
        geometries = layout_json_to_geometries(layout)
    except Exception as e:
        logger.exception("Layout to geometries failed")
        raise HTTPException(422, f"Could not parse layout coordinates: {e}")

    if not geometries:
        raise HTTPException(422, "No valid objects or room boundary found in the JSON.")

    # 1. Nudge overlapping objects apart (constrained by room walls)
    geometries_nudged, nudge_reports = nudge_overlaps(geometries)
    nudge_errors = [r for r in nudge_reports if not r.get("success")]

    # 2. Evaluate rules on the (nudged) layout
    violations = evaluate_rules(geometries_nudged)
    result = format_response(violations, nudge_reports=nudge_reports, nudge_errors=nudge_errors)

    # 3. Space evaluation: for each violation, how much can we move objects to fix it?
    space_results = []
    for v in violations:
        if v.get("type") in ("min_clearance", "coffee_table_sofa_range", "proximity_range") and v.get("objects"):
            space_results.append(space_evaluation(geometries_nudged, v))
    result["space_evaluation"] = space_results

    result["objects_found"] = list(geometries_nudged.keys())
    result["shapely_geometry"] = get_shapely_geometry_output(geometries)
    nudge_applied = len([r for r in nudge_reports if r.get("success")])
    nudge_failed = len(nudge_errors)
    result["nudge_applied"] = nudge_applied
    result["nudge_failed"] = nudge_failed
    result["layout_after_nudge"] = geometries_to_layout(geometries_nudged)
    result["summary"] = _build_summary(
        result["valid"], result["violation_count"], nudge_applied, nudge_failed
    )
    result["response_guide"] = RESPONSE_GUIDE
    return result


@router.get("/example-layout")
async def get_example_layout():
    """Return the example OpenCV-style layout JSON for testing."""
    if not EXAMPLE_LAYOUT_PATH.exists():
        raise HTTPException(404, "Example layout file not found.")
    data = json.loads(EXAMPLE_LAYOUT_PATH.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


@router.post("/blueprint")
async def get_blueprint(request: Request):
    """
    Generate a blueprint diagram from layout JSON (room + objects with coordinates).
    Accepts JSON body; returns PNG image. Use after uploading a layout file.
    """
    try:
        layout = await request.json()
    except Exception as e:
        raise HTTPException(400, f"Invalid JSON body: {e}")
    try:
        png_bytes = layout_to_blueprint_png(layout)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception("Blueprint generation failed")
        raise HTTPException(500, str(e))
    return Response(content=png_bytes, media_type="image/png")
