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
from shapely.ops import nearest_points

ROOM_KEY = "room"

# Nudge step size (mm) and max steps to avoid infinite loops
NUDGE_STEP_MM = 5   # finer steps so we don't overshoot when near walls
NUDGE_MAX_STEPS = 4000
# Default minimum gap (mm) when no rule applies for a pair (fallback so objects don't appear touching)
DEFAULT_MIN_GAP_MM = 10
# Prefer axis-aligned nudge (only up/down or only left/right) so objects move in one direction, not diagonal
PREFER_AXIS_ALIGNED_NUDGE = True


def get_required_gap_mm(a: str, b: str, rules: list[dict[str, Any]] | None) -> float:
    """
    Required clearance (mm) between objects a and b from rules.
    - If the pair has a proximity_range (e.g. coffee table to sofa 350–450 mm), use only that min
      so we nudge to the range instead of pushing to min_clearance (600 mm) and causing "too far".
    - Otherwise use max(min_clearance global, any pair-specific min).
    If no rules or no applicable rule, returns DEFAULT_MIN_GAP_MM.
    """
    if not rules:
        return DEFAULT_MIN_GAP_MM
    global_min: float = 0.0
    pair_min: float = 0.0
    pair_has_proximity_range = False
    # Normalize like rule parser: lower, spaces -> underscores
    an = a.strip().lower().replace(" ", "_").replace("-", "_")
    bn = b.strip().lower().replace(" ", "_").replace("-", "_")
    pair_key = (an, bn)
    pair_key_rev = (bn, an)
    for r in rules:
        rtype = r.get("type") or ""
        if rtype == "min_clearance":
            m = r.get("min_mm")
            if m is not None:
                global_min = max(global_min, float(m))
        elif rtype == "proximity_range":
            oa = (r.get("object_a") or "").strip().lower()
            ob = (r.get("object_b") or "").strip().lower()
            if (oa, ob) == pair_key or (oa, ob) == pair_key_rev:
                pair_has_proximity_range = True
                m = r.get("min_mm")
                if m is not None:
                    pair_min = max(pair_min, float(m))
    # For proximity_range pairs (e.g. coffee table–sofa 350–450), nudge only to min (350), not to 600
    if pair_has_proximity_range and pair_min > 0:
        return pair_min
    out = max(global_min, pair_min)
    return out if out > 0 else DEFAULT_MIN_GAP_MM


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


def _dedupe_nudge_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per object pair; prefer success over failure when both exist (same pair can be attempted in multiple rounds)."""
    by_pair: dict[frozenset[str], dict[str, Any]] = {}
    order: list[frozenset[str]] = []
    for r in reports:
        objs = r.get("objects") or []
        if len(objs) < 2:
            continue
        pair = frozenset(objs[:2])
        if pair not in by_pair:
            by_pair[pair] = r
            order.append(pair)
        elif r.get("success") and not by_pair[pair].get("success"):
            by_pair[pair] = r  # replace failure with success
    return [by_pair[p] for p in order]


def _append_success_report(
    reports: list[dict[str, Any]],
    a: str,
    b: str,
    nudged: str,
    distance_mm: float,
    required_gap_mm: float,
    achieved_gap_mm: float,
    reason: str | None = None,
) -> None:
    """Append a success report and remove any prior failed report for the same pair (so final outcome is clear)."""
    pair_set = {a, b}
    def is_same_pair_failed(r: dict) -> bool:
        objs = r.get("objects") or []
        if len(objs) < 2:
            return False
        return set(objs[:2]) == pair_set and r.get("success") is False
    reports[:] = [r for r in reports if not is_same_pair_failed(r)]
    entry: dict[str, Any] = {
        "objects": [a, b],
        "nudged": nudged,
        "success": True,
        "distance_mm": distance_mm,
        "required_gap_mm": required_gap_mm,
        "achieved_gap_mm": round(achieved_gap_mm, 2),
    }
    if reason:
        entry["reason"] = reason
    reports.append(entry)


def _direction_away_from_polygons(poly_a: Polygon, poly_b: Polygon) -> tuple[float, float]:
    """
    Unit vector to move poly_a away from poly_b, using the nearest points on their boundaries.
    If PREFER_AXIS_ALIGNED_NUDGE is True, returns only the dominant axis (vertical or horizontal)
    so the object moves purely up/down or left/right, not diagonally.
    If polygons overlap or nearest points coincide, falls back to centroid-to-centroid.
    """
    pt_a, pt_b = nearest_points(poly_a, poly_b)
    dx = pt_a.x - pt_b.x
    dy = pt_a.y - pt_b.y
    d = math.hypot(dx, dy)
    if d < 1e-6:
        ca = _centroid(poly_a)
        cb = _centroid(poly_b)
        dx, dy = ca[0] - cb[0], ca[1] - cb[1]
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return (1.0, 0.0)
    if PREFER_AXIS_ALIGNED_NUDGE:
        # Use only the dominant axis so nudge is purely vertical or purely horizontal
        if abs(dy) >= abs(dx):
            return (0.0, 1.0 if dy >= 0 else -1.0)
        return (1.0 if dx >= 0 else -1.0, 0.0)
    return (dx / d, dy / d)


def nudge_overlaps(
    geometries: dict[str, Polygon],
    rules: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Polygon], list[dict[str, Any]]]:
    """
    Resolve overlaps by nudging objects apart. All moves are constrained by the room polygon.
    Required gap after nudge is taken from rules (min_clearance / proximity_range) per object pair;
    if no rule applies, uses DEFAULT_MIN_GAP_MM.
    Returns (updated_geometries, nudge_reports).
    nudge_reports: list of { "objects": [a,b], "nudged": a|b, "success": bool, "reason": str?, "required_gap_mm": float? }
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
                required_gap = get_required_gap_mm(a, b, rules)
                # Nudge when overlapping/touching or when gap is below required (rule-based or default)
                if furniture[a].intersects(furniture[b]) or furniture[a].distance(furniture[b]) < required_gap:
                    overlapping.append((a, b))
        if not overlapping:
            break

        for a, b in overlapping:
            poly_a = furniture[a]
            poly_b = furniture[b]
            required_gap = get_required_gap_mm(a, b, rules)
            dx_u, dy_u = _direction_away_from_polygons(poly_a, poly_b)
            success = False

            # Strategy 1: move BOTH in opposite directions (needs least clearance in one direction) — try first
            for step in range(1, NUDGE_MAX_STEPS):
                off_half = (step * NUDGE_STEP_MM) / 2.0
                new_a = translate(poly_a, xoff=dx_u * off_half, yoff=dy_u * off_half)
                new_b = translate(poly_b, xoff=-dx_u * off_half, yoff=-dy_u * off_half)
                gap = new_a.distance(new_b)
                if not new_a.intersects(new_b) and gap >= required_gap:
                    in_room_a = room.contains(new_a) or new_a.within(room)
                    in_room_b = room.contains(new_b) or new_b.within(room)
                    if in_room_a and in_room_b:
                        ok = True
                        for other in names:
                            if other in (a, b):
                                continue
                            if new_a.intersects(furniture[other]) or new_b.intersects(furniture[other]):
                                ok = False
                                break
                        if ok and gap >= required_gap:
                            furniture[a] = new_a
                            furniture[b] = new_b
                            geos[a] = new_a
                            geos[b] = new_b
                            _append_success_report(reports, a, b, "both", step * NUDGE_STEP_MM, required_gap, gap, "Moved both objects apart (half each).")
                            success = True
                            break
            if success:
                continue

            # Strategy 2: push A away from B
            for step in range(1, NUDGE_MAX_STEPS):
                off = step * NUDGE_STEP_MM
                new_a = translate(poly_a, xoff=dx_u * off, yoff=dy_u * off)
                gap = new_a.distance(poly_b)
                if not new_a.intersects(poly_b) and gap >= required_gap:
                    if room.contains(new_a) or new_a.within(room):
                        ok = True
                        for other in names:
                            if other in (a, b):
                                continue
                            if new_a.intersects(furniture[other]):
                                ok = False
                                break
                        if ok and gap >= required_gap:
                            furniture[a] = new_a
                            geos[a] = new_a
                            _append_success_report(reports, a, b, a, off, required_gap, gap)
                            success = True
                            break
                    else:
                        break
            if success:
                continue

            # Strategy 3: push B away from A
            dx_u, dy_u = _direction_away_from_polygons(poly_b, poly_a)
            for step in range(1, NUDGE_MAX_STEPS):
                off = step * NUDGE_STEP_MM
                new_b = translate(poly_b, xoff=dx_u * off, yoff=dy_u * off)
                gap = new_b.distance(furniture[a])
                if not new_b.intersects(furniture[a]) and gap >= required_gap:
                    if room.contains(new_b) or new_b.within(room):
                        ok = True
                        for other in names:
                            if other in (a, b):
                                continue
                            if new_b.intersects(furniture[other]):
                                ok = False
                                break
                        if ok and gap >= required_gap:
                            furniture[b] = new_b
                            geos[b] = new_b
                            _append_success_report(reports, a, b, b, off, required_gap, gap)
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

    # One entry per pair: prefer success over failure (same pair can be attempted in multiple rounds)
    reports = _dedupe_nudge_reports(reports)
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
        dx_u, dy_u = _direction_away_from_polygons(poly_a, poly_b)
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
