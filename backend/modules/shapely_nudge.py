"""
Shapely Layout Demo — Nudge & space evaluation

1. Nudge overlapping objects apart, constrained by room walls.
   If a nudge would move an object outside the room, try the other object; if both fail, report "nudge not possible".
2. After nudging, rule evaluation runs on the updated layout.
3. Space evaluation: for violations, estimate how much an object can be moved to fix the issue (within room, no new overlaps).
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from shapely.affinity import translate
from shapely.geometry import Polygon

ROOM_KEY = "room"

# Nudge step size (mm) and max steps to avoid infinite loops
NUDGE_STEP_MM = 10
NUDGE_MAX_STEPS = 2000


def _centroid(poly: Polygon) -> tuple[float, float]:
    c = poly.centroid
    return (c.x, c.y)


def _direction_away_from(from_xy: tuple[float, float], to_xy: tuple[float, float]) -> tuple[float, float]:
    """Unit vector from from_xy toward to_xy (so moving to_xy further away from from_xy)."""
    dx = to_xy[0] - from_xy[0]
    dy = to_xy[1] - from_xy[1]
    d = math.hypot(dx, dy)
    if d < 1e-6:
        return (1.0, 0.0)
    return (dx / d, dy / d)


def nudge_overlaps(geometries: dict[str, Polygon]) -> tuple[dict[str, Polygon], list[dict[str, Any]]]:
    """
    Resolve overlaps by nudging objects apart. All moves are constrained by the room polygon.
    Returns (updated_geometries, nudge_reports).
    nudge_reports: list of { "objects": [a,b], "nudged": a|b, "success": bool, "reason": str? }
    """
    geos = deepcopy(geometries)
    room = geos.get(ROOM_KEY)
    furniture = {k: v for k, v in geos.items() if k != ROOM_KEY}
    names = list(furniture.keys())
    reports: list[dict[str, Any]] = []

    if not room or len(names) < 2:
        return geos, reports

    # Iteratively resolve overlaps; skip pairs we've already failed to nudge
    failed_pairs: set[tuple[str, str]] = set()
    max_rounds = 50
    for _ in range(max_rounds):
        overlapping = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                if (a, b) in failed_pairs or (b, a) in failed_pairs:
                    continue
                if furniture[a].intersects(furniture[b]):
                    overlapping.append((a, b))
        if not overlapping:
            break

        for a, b in overlapping:
            poly_a = furniture[a]
            poly_b = furniture[b]
            ca = _centroid(poly_a)
            cb = _centroid(poly_b)
            # Push A away from B
            dx_u, dy_u = _direction_away_from(cb, ca)
            success = False
            for step in range(1, NUDGE_MAX_STEPS):
                off = step * NUDGE_STEP_MM
                new_a = translate(poly_a, xoff=dx_u * off, yoff=dy_u * off)
                if not new_a.intersects(poly_b):
                    if room.contains(new_a) or new_a.within(room):
                        # No overlap with other furniture
                        ok = True
                        for other in names:
                            if other in (a, b):
                                continue
                            if new_a.intersects(furniture[other]):
                                ok = False
                                break
                        if ok:
                            furniture[a] = new_a
                            geos[a] = new_a
                            reports.append({"objects": [a, b], "nudged": a, "success": True, "distance_mm": off})
                            success = True
                            break
                    else:
                        # A would go outside room; try nudging B instead
                        break
            if success:
                continue
            # Try moving B away from A
            dx_u, dy_u = _direction_away_from(ca, cb)
            for step in range(1, NUDGE_MAX_STEPS):
                off = step * NUDGE_STEP_MM
                new_b = translate(poly_b, xoff=dx_u * off, yoff=dy_u * off)
                if not new_b.intersects(furniture[a]):
                    if room.contains(new_b) or new_b.within(room):
                        ok = True
                        for other in names:
                            if other in (a, b):
                                continue
                            if new_b.intersects(furniture[other]):
                                ok = False
                                break
                        if ok:
                            furniture[b] = new_b
                            geos[b] = new_b
                            reports.append({"objects": [a, b], "nudged": b, "success": True, "distance_mm": off})
                            success = True
                            break
                    else:
                        break
            if not success:
                failed_pairs.add((a, b))
                reports.append({
                    "objects": [a, b],
                    "nudged": None,
                    "success": False,
                    "reason": "Nudge would place object outside room; no valid move found.",
                })

    return geos, reports


def space_evaluation(
    geometries: dict[str, Polygon],
    violation: dict[str, Any],
    step_mm: float = 50,
) -> dict[str, Any]:
    """
    For a clearance/distance violation, estimate how much the object(s) can be moved to fix it.
    Returns e.g. { "object": "coffee_table", "move_away_mm": 300, "move_toward_mm": 0, "blocked_by": "wall" }.
    """
    room = geometries.get(ROOM_KEY)
    furniture = {k: v for k, v in geometries.items() if k != ROOM_KEY}
    if not room or not violation.get("objects"):
        return {"error": "missing room or objects"}

    objs = violation["objects"]
    vtype = violation.get("type") or violation.get("detail", "")
    result: dict[str, Any] = {"violation_rule": violation.get("rule"), "objects": objs}

    # For a pair (a, b): how far can we move a away from b before hitting room boundary or another object?
    if len(objs) >= 2 and objs[0] in furniture and objs[1] in furniture:
        a, b = objs[0], objs[1]
        poly_a = furniture[a]
        poly_b = furniture[b]
        ca = _centroid(poly_a)
        cb = _centroid(poly_b)
        dx_u, dy_u = _direction_away_from(cb, ca)
        move_away_mm = 0
        for k in range(1, 200):
            off = k * step_mm
            new_a = translate(poly_a, xoff=dx_u * off, yoff=dy_u * off)
            if not (room.contains(new_a) or new_a.within(room)):
                result["move_away_mm"] = move_away_mm
                result["blocked_by"] = "room_boundary"
                result["object_moved"] = a
                return result
            if new_a.intersects(poly_b):
                continue
            # No longer intersecting B; we have at least this much room
            move_away_mm = off
            # Check other furniture
            for name, poly in furniture.items():
                if name in (a, b):
                    continue
                if new_a.intersects(poly):
                    result["move_away_mm"] = move_away_mm
                    result["blocked_by"] = name
                    result["object_moved"] = a
                    return result
        result["move_away_mm"] = move_away_mm
        result["object_moved"] = a
        result["blocked_by"] = None
        return result

    # Object vs room_boundary: how far can the object move toward the room interior (away from the wall)?
    if len(objs) >= 2 and objs[0] in furniture and objs[1] == "room_boundary" and room:
        a = objs[0]
        poly_a = furniture[a]
        room_centroid = _centroid(room)
        ca = _centroid(poly_a)
        # Direction from object toward room center (move toward interior)
        dx_u, dy_u = _direction_away_from(ca, room_centroid)
        move_away_mm = 0
        for k in range(1, 200):
            off = k * step_mm
            new_a = translate(poly_a, xoff=dx_u * off, yoff=dy_u * off)
            if not (room.contains(new_a) or new_a.within(room)):
                result["move_away_mm"] = move_away_mm
                result["blocked_by"] = "room_boundary"
                result["object_moved"] = a
                return result
            move_away_mm = off
            # Check other furniture (don't overlap them)
            for name, poly in furniture.items():
                if name == a:
                    continue
                if new_a.intersects(poly):
                    result["move_away_mm"] = move_away_mm
                    result["blocked_by"] = name
                    result["object_moved"] = a
                    return result
        result["move_away_mm"] = move_away_mm
        result["object_moved"] = a
        result["blocked_by"] = None
        return result

    # Single object (e.g. outside room): no second object to compute against
    if len(objs) >= 1 and objs[0] in furniture:
        a = objs[0]
        result["object_moved"] = a
        result["move_away_mm"] = None
        result["blocked_by"] = None
        return result

    return result
