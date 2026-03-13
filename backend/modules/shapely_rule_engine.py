"""
Shapely Layout Demo — Rule Engine

Converts layout JSON (OpenCV-style coordinates) to Shapely geometries and evaluates
spatial rules using Shapely (intersects, distance, within).
Rules are loaded from SHAPELY_LAYOUT_RULES.md via the rule parser; no hardcoded rules.
"""

from __future__ import annotations

import logging
from typing import Any

from shapely.geometry import Polygon

from modules.shapely_rule_parser import parse_rules_file

logger = logging.getLogger(__name__)

# Assume coordinates in layout JSON are in mm (same unit as rules)
ROOM_KEY = "room"  # key for room boundary in layout JSON
# Object names that can provide room boundary if "room" / "room_boundary" are not present
ROOM_ALIASES = ("room", "room_boundary", "wall")


def _coords_to_polygon(coords: list[list[float]] | list[tuple[float, float]]) -> Polygon | None:
    """Convert list of [x,y] or (x,y) to Shapely Polygon. Needs at least 3 points."""
    if not coords or len(coords) < 3:
        return None
    try:
        flat = [(float(c[0]), float(c[1])) for c in coords]
        # Close ring if not closed
        if flat[0] != flat[-1]:
            flat.append(flat[0])
        return Polygon(flat)
    except (TypeError, IndexError, ValueError):
        return None


def layout_json_to_geometries(layout: dict[str, Any]) -> dict[str, Polygon]:
    """
    Parse layout JSON: expect objects key with name -> coordinates.
    Coordinates can be:
      - "bbox" or "polygon" or "coords": list of [x,y] points
      - or a list of [x,y] at top level for that object
    Returns dict mapping object name -> Shapely Polygon.
    """
    geometries = {}
    objects = layout.get("objects", layout)  # some formats put everything under "objects"

    if isinstance(objects, list):
        for item in objects:
            name = item.get("name") or item.get("object") or item.get("label") or item.get("id")
            coords = item.get("coords") or item.get("polygon") or item.get("bbox") or item.get("coordinates")
            if name and coords:
                poly = _coords_to_polygon(coords)
                if poly and poly.is_valid:
                    geometries[str(name)] = poly
        # Room boundary: prefer "room" or "room_boundary", then "wall" (so user can send wall as boundary)
        room = None
        for key in ROOM_ALIASES:
            room = layout.get(key)
            if room is not None:
                break
        if room:
            coords = room.get("coords") or room.get("polygon") if isinstance(room, dict) else room
            if isinstance(coords, list):
                poly = _coords_to_polygon(coords)
                if poly and poly.is_valid:
                    geometries[ROOM_KEY] = poly
        return geometries

    # Dict format: { "sofa": [[x,y],[x,y],...], "room": [...] }
    for name, value in objects.items() if isinstance(objects, dict) else layout.items():
        if name in ROOM_ALIASES and isinstance(value, (list, dict)):
            coords = value if isinstance(value, list) else (value.get("coords") or value.get("polygon"))
            if coords:
                poly = _coords_to_polygon(coords)
                if poly and poly.is_valid:
                    geometries[ROOM_KEY] = poly
            continue
        if isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
            poly = _coords_to_polygon(value)
            if poly and poly.is_valid:
                geometries[str(name)] = poly
    return geometries


def geometries_to_layout(geometries: dict[str, Polygon]) -> dict[str, Any]:
    """Serialize geometries back to layout-like dict: room + objects with coords (for nudged layout)."""
    room = geometries.get(ROOM_KEY)
    out: dict[str, Any] = {}
    if room:
        coords = [[float(c[0]), float(c[1])] for c in room.exterior.coords][:-1]  # drop closing point
        out["room"] = coords
    objects = []
    for name, poly in geometries.items():
        if name == ROOM_KEY:
            continue
        coords = [[float(c[0]), float(c[1])] for c in poly.exterior.coords][:-1]
        objects.append({"name": name, "coords": coords})
    out["objects"] = objects
    return out


def _distance_mm(poly_a: Polygon, poly_b: Polygon) -> float:
    """
    Edge-to-edge distance between two polygons (minimum distance between their boundaries).
    Same units as input (assumed mm). Not center-to-center; this is the clearance gap.
    """
    return float(poly_a.distance(poly_b))


def get_shapely_geometry_output(geometries: dict[str, Polygon]) -> dict[str, Any]:
    """
    Return raw Shapely outputs only (no rule logic): distances and intersects/within_room.
    Used to show on UI what the Shapely package returns before rules are applied.
    """
    room = geometries.get(ROOM_KEY)
    furniture = {k: v for k, v in geometries.items() if k != ROOM_KEY}
    names = list(furniture.keys())

    object_pairs: list[dict[str, Any]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            poly_a, poly_b = furniture[a], furniture[b]
            d = _distance_mm(poly_a, poly_b)
            object_pairs.append({
                "a": a,
                "b": b,
                "distance_mm": round(d, 2),
                "distance_type": "edge_to_edge",
                "intersects": bool(poly_a.intersects(poly_b)),
            })

    object_to_room: list[dict[str, Any]] = []
    if room:
        for n in names:
            poly = furniture[n]
            d = _distance_mm(poly, room.boundary) if hasattr(room, "boundary") else _distance_mm(poly, room)
            object_to_room.append({
                "object": n,
                "distance_to_boundary_mm": round(d, 2),
                "within_room": bool(poly.within(room) or room.contains(poly)),
            })

    return {
        "object_pairs": object_pairs,
        "object_to_room": object_to_room,
        "distance_note": "distance_mm is minimum edge-to-edge (boundary) clearance, not center-to-center.",
    }


def evaluate_rules(geometries: dict[str, Polygon], rules: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """
    Evaluate rules from SHAPELY_LAYOUT_RULES.md against the given geometries.
    Returns list of violations. Each violation includes rule, type, objects, distance, detail, and optional description.
    """
    if rules is None:
        rules = parse_rules_file()
    if not rules:
        logger.warning("No rules loaded from rules file; layout will not be validated.")
    all_rules = rules
    violations = []
    room = geometries.get(ROOM_KEY)
    furniture = {k: v for k, v in geometries.items() if k != ROOM_KEY}
    names = list(furniture.keys())

    # Pairs that have a proximity_range rule (e.g. coffee table–sofa 350–450 mm): skip min_clearance for them
    pairs_with_proximity_range: set[frozenset[str]] = set()
    for r in all_rules:
        if r.get("type") == "proximity_range" and r.get("object_a") and r.get("object_b"):
            oa = (r.get("object_a") or "").strip().lower().replace(" ", "_").replace("-", "_")
            ob = (r.get("object_b") or "").strip().lower().replace(" ", "_").replace("-", "_")
            pairs_with_proximity_range.add(frozenset([oa, ob]))

    def _norm(s: str) -> str:
        return s.strip().lower().replace(" ", "_").replace("-", "_")

    for rule in all_rules:
        rid = rule.get("rule_id") or ""
        rtype = rule.get("type") or ""
        description = rule.get("description") or ""

        if rtype == "no_overlap":
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a, b = names[i], names[j]
                    if furniture[a].intersects(furniture[b]):
                        v = {
                            "rule": rid,
                            "type": "no_overlap",
                            "objects": [a, b],
                            "distance": 0,
                            "detail": "overlap",
                        }
                        if description:
                            v["description"] = description
                        violations.append(v)

        elif rtype == "min_clearance":
            min_mm = rule.get("min_mm", 600)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a, b = names[i], names[j]
                    if frozenset([_norm(a), _norm(b)]) in pairs_with_proximity_range:
                        continue  # this pair has a proximity_range (e.g. 350–450 mm); don't require 600 mm
                    d = _distance_mm(furniture[a], furniture[b])
                    if d < min_mm:
                        v = {
                            "rule": rid,
                            "type": "min_clearance",
                            "objects": [a, b],
                            "distance": round(d, 2),
                            "min_required": min_mm,
                            "detail": "clearance",
                        }
                        if description:
                            v["description"] = description
                        violations.append(v)
            if room:
                for n in names:
                    d = _distance_mm(furniture[n], room.boundary) if hasattr(room, "boundary") else _distance_mm(furniture[n], room)
                    if d < min_mm:
                        v = {
                            "rule": rid,
                            "type": "min_clearance",
                            "objects": [n, "room_boundary"],
                            "distance": round(d, 2),
                            "min_required": min_mm,
                            "detail": "clearance",
                        }
                        if description:
                            v["description"] = description
                        violations.append(v)

        elif rtype == "proximity_range":
            min_mm = rule.get("min_mm", 350)
            max_mm = rule.get("max_mm", 450)
            object_a = rule.get("object_a")
            object_b = rule.get("object_b")
            if object_a is not None and object_b is not None:
                if object_a in furniture and object_b in furniture:
                    d = _distance_mm(furniture[object_a], furniture[object_b])
                    if d < min_mm:
                        v = {
                            "rule": rid,
                            "type": "proximity_range",
                            "objects": [object_a, object_b],
                            "distance": round(d, 2),
                            "min_required": min_mm,
                            "max_required": max_mm,
                            "detail": "too_close",
                        }
                        if description:
                            v["description"] = description
                        violations.append(v)
                    elif max_mm is not None and d > max_mm:
                        v = {
                            "rule": rid,
                            "type": "proximity_range",
                            "objects": [object_a, object_b],
                            "distance": round(d, 2),
                            "min_required": min_mm,
                            "max_required": max_mm,
                            "detail": "too_far",
                        }
                        if description:
                            v["description"] = description
                        violations.append(v)
            # If object_a/object_b not set, skip (avoid duplicating min_clearance over all pairs)

        elif rtype == "inside_room":
            if not room:
                continue
            for n in names:
                if not furniture[n].within(room) and not room.contains(furniture[n]):
                    v = {
                        "rule": rid,
                        "type": "inside_room",
                        "objects": [n],
                        "distance": None,
                        "detail": "outside_room",
                    }
                    if description:
                        v["description"] = description
                    violations.append(v)

    return violations
