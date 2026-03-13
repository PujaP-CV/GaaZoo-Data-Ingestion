"""
Shapely Layout Adapter — CoreModel format to canonical layout.

Converts the coreModel-style JSON (floors, objects, walls with transform + dimensions)
into the canonical layout format expected by layout_json_to_geometries:
  { "room": [[x,y], ...], "objects": [ { "name": "...", "coords": [[x,y], ...] } ] }
Coordinates are output in millimetres (rules use mm). Input transform/dimensions are in metres.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Input is in metres; rules use mm
_M_TO_MM = 1000.0


def _is_core_model_format(layout: dict[str, Any]) -> bool:
    """True if layout has floors (list), objects (list) with transform+dimensions (no coords)."""
    if not isinstance(layout.get("objects"), list) or not layout.get("objects"):
        return False
    first = layout["objects"][0]
    if not isinstance(first, dict):
        return False
    has_transform = "transform" in first and isinstance(first["transform"], (list, tuple)) and len(first["transform"]) >= 16
    has_dimensions = "dimensions" in first and isinstance(first["dimensions"], (list, tuple)) and len(first["dimensions"]) >= 2
    has_category = "category" in first and isinstance(first["category"], dict)
    no_coords = "coords" not in first and "polygon" not in first
    return bool(has_transform and has_dimensions and (has_category or no_coords))


def _transform_position(t: list[float] | tuple[float, ...]) -> tuple[float, float, float]:
    """Extract position (tx, ty, tz) from 4x4 transform (row-major: position at 12,13,14)."""
    if len(t) < 15:
        return (0.0, 0.0, 0.0)
    return (float(t[12]), float(t[13]), float(t[14]))


def _transform_axes_2d(t: list[float] | tuple[float, ...]) -> tuple[tuple[float, float], tuple[float, float], str]:
    """Return (axis0_2d, axis1_2d, plane) from 4x4 transform for horizontal footprint.
    Row-major: col0 = (t[0],t[4],t[8]), col2 = (t[2],t[6],t[10]). If col0.z and col2.z are ~0, floor is in XY (use axis0_xy, axis1_xy); else use XZ.
    """
    if len(t) < 11:
        return ((1.0, 0.0), (0.0, 1.0), "xz")
    right_x, right_y, right_z = float(t[0]), float(t[4]), float(t[8])
    fwd_x, fwd_y, fwd_z = float(t[2]), float(t[6]), float(t[10])
    # If both axes have negligible Z, rectangle is in XY (e.g. Y-up with horizontal floor in XZ actually means we want XZ for top-down; try XZ first)
    use_xy = abs(right_z) < 1e-6 and abs(fwd_z) < 1e-6
    if use_xy:
        # Horizontal plane is XY: use (x, y) for both axes
        axis0 = (right_x, right_y)
        axis1 = (fwd_x, fwd_y)
        plane = "xy"
    else:
        # Horizontal plane is XZ: use (x, z) for both axes
        axis0 = (right_x, right_z)
        axis1 = (fwd_x, fwd_z)
        plane = "xz"
    return (axis0, axis1, plane)


def _rect_footprint_m(
    pos_a: float, pos_b: float,
    axis0: tuple[float, float], axis1: tuple[float, float],
    half_w: float, half_d: float,
) -> list[list[float]]:
    """Four corners in metres in the horizontal plane, then convert to mm. Returns [[u_mm, v_mm], ...] for canonical."""
    corners_local = [
        (half_w, half_d),
        (-half_w, half_d),
        (-half_w, -half_d),
        (half_w, -half_d),
    ]
    out = []
    for lx, lz in corners_local:
        u = pos_a + axis0[0] * lx + axis1[0] * lz
        v = pos_b + axis0[1] * lx + axis1[1] * lz
        out.append([u * _M_TO_MM, v * _M_TO_MM])
    return out


def _category_to_name(category: dict[str, Any], occurrence_index: int) -> str:
    """Derive a short name from category (e.g. { "table": {} } -> table, table_2 for 2nd)."""
    if not category or not isinstance(category, dict):
        name = "object"
    else:
        keys = [k for k in category if isinstance(category.get(k), dict) or category.get(k) is None]
        name = keys[0] if keys else "object"
    # Normalize to snake_case for rule matching (e.g. coffee table -> coffee_table, television -> tv)
    name = str(name).lower().replace(" ", "_").replace("-", "_")
    if name == "television":
        name = "tv"
    if occurrence_index > 0:
        name = f"{name}_{occurrence_index + 1}"
    return name


def _build_room_from_floor(floor: dict[str, Any]) -> list[list[float]] | None:
    """Build room polygon (in mm) from floor dimensions and transform."""
    dims = floor.get("dimensions")
    t = floor.get("transform")
    if not dims or len(dims) < 2 or not t or len(t) < 15:
        return None
    try:
        Lx = float(dims[0])
        Lz = float(dims[1]) if len(dims) > 1 else float(dims[0])
    except (TypeError, ValueError):
        return None
    tx, ty, tz = _transform_position(t)
    axis0, axis1, plane = _transform_axes_2d(t)
    half_w, half_d = Lx / 2.0, Lz / 2.0
    if plane == "xy":
        return _rect_footprint_m(tx, ty, axis0, axis1, half_w, half_d)
    return _rect_footprint_m(tx, tz, axis0, axis1, half_w, half_d)


def _build_object_footprint(obj: dict[str, Any]) -> list[list[float]] | None:
    """Build object polygon (in mm) from dimensions and transform. Uses width (dims[0]) and depth (dims[2])."""
    dims = obj.get("dimensions")
    t = obj.get("transform")
    if not dims or len(dims) < 2 or not t or len(t) < 15:
        return None
    try:
        w = float(dims[0])
        d = float(dims[2]) if len(dims) > 2 else float(dims[1])
    except (TypeError, ValueError, IndexError):
        return None
    tx, ty, tz = _transform_position(t)
    axis0, axis1, plane = _transform_axes_2d(t)
    half_w, half_d = w / 2.0, d / 2.0
    if plane == "xy":
        return _rect_footprint_m(tx, ty, axis0, axis1, half_w, half_d)
    return _rect_footprint_m(tx, tz, axis0, axis1, half_w, half_d)


def core_model_to_canonical_layout(layout: dict[str, Any]) -> dict[str, Any]:
    """
    Convert coreModel-style layout to canonical layout (room + objects with coords in mm).

    Input: layout with "floors", "objects", optional "walls". Each floor/object has
      "dimensions" (list, metres) and "transform" (4x4 column-major). Objects have "category" (e.g. {"table": {}}).
    Output: { "room": [[x_mm, y_mm], ...], "objects": [ { "name": "...", "coords": [[x_mm, y_mm], ...] } ] }.
    """
    canonical: dict[str, Any] = {"objects": []}

    # Room from first floor
    floors = layout.get("floors")
    if isinstance(floors, list) and floors and isinstance(floors[0], dict):
        room_coords = _build_room_from_floor(floors[0])
        if room_coords and len(room_coords) >= 3:
            canonical["room"] = room_coords
        else:
            logger.warning("Could not build room polygon from floors.")
    else:
        logger.warning("No floors list found for room boundary.")

    # Objects: unique names per category (table, table_2, chair, chair_2, tv, ...)
    name_counts: dict[str, int] = {}
    for obj in layout.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        category = obj.get("category") or {}
        base_name = _category_to_name(category, 0)
        count = name_counts.get(base_name, 0)
        name_counts[base_name] = count + 1
        name = _category_to_name(category, count)
        coords = _build_object_footprint(obj)
        if coords and len(coords) >= 3:
            canonical["objects"].append({"name": name, "coords": coords})
        else:
            logger.debug("Skipping object %s: could not build footprint.", name)

    return canonical


def normalize_layout_for_shapely(layout: dict[str, Any]) -> dict[str, Any]:
    """
    If layout is in coreModel format, convert to canonical; otherwise return as-is.
    Use this before layout_json_to_geometries so the pipeline accepts both formats.
    """
    if _is_core_model_format(layout):
        return core_model_to_canonical_layout(layout)
    return layout
