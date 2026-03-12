"""
Shapely Layout Demo — Response Formatter

Turns technical rule violations into human-readable explanations.
"""

from typing import Any


def format_violation_explanation(v: dict[str, Any]) -> str:
    """Convert a single violation dict to a human-readable sentence. Uses rule description from file when present."""
    rule = v.get("rule", "")
    objs = v.get("objects", [])
    dist = v.get("distance")
    detail = v.get("detail", "")
    description = v.get("description", "")
    suffix = f" (Rule from document: {description})" if description else ""

    if v.get("type") == "no_overlap" or detail == "overlap":
        a, b = (objs[0], objs[1]) if len(objs) >= 2 else (objs[0], "another object")
        return f"The {a.replace('_', ' ')} is overlapping with the {b.replace('_', ' ')}." + suffix

    if v.get("type") == "min_clearance" or detail == "clearance":
        min_req = v.get("min_required", 600)
        a = objs[0] if objs else "object"
        b = objs[1] if len(objs) > 1 else "room boundary"
        dist_str = f"{int(round(dist))} mm" if dist is not None else "0 mm"
        return (
            f"Minimum clearance between {a.replace('_', ' ')} and {b.replace('_', ' ')} is not met. "
            f"Current distance is {dist_str} but minimum required clearance is {min_req} mm." + suffix
        )

    if v.get("type") in ("coffee_table_sofa_range", "proximity_range"):
        min_req = v.get("min_required", 350)
        max_req = v.get("max_required")
        a = objs[0].replace("_", " ") if len(objs) > 0 else "object"
        b = objs[1].replace("_", " ") if len(objs) > 1 else "other"
        dist_str = f"{int(round(dist))} mm" if dist is not None else "N/A"
        if detail == "too_close":
            if max_req is not None:
                return f"The {a} is too close to the {b}. Current distance is {dist_str} but minimum required is {min_req} mm (ideal range {min_req}–{max_req} mm)." + suffix
            return f"The {a} is too close to the {b}. Current distance is {dist_str} but minimum required is {min_req} mm." + suffix
        if detail == "too_far" and max_req is not None:
            return f"The {a} is too far from the {b}. Current distance is {dist_str} but should be between {min_req} and {max_req} mm." + suffix

    if v.get("type") == "inside_room" or detail == "outside_room":
        a = objs[0] if objs else "An object"
        return f"The {a.replace('_', ' ')} is not fully inside the room boundary." + suffix

    if description:
        return f"Rule {rule} violated: {objs}. Distance: {dist}. Rule from document: {description}."
    return f"Rule {rule} violated: {objs}. Distance: {dist}."


def format_nudge_report(r: dict[str, Any]) -> str:
    """Human-readable line for a single nudge report."""
    objs = r.get("objects", [])
    a, b = (objs[0], objs[1]) if len(objs) >= 2 else (objs[0], "other")
    nudged = r.get("nudged") or "object"
    if r.get("success"):
        # "Away from" = the other object in the pair (the one that was not nudged)
        other = b if nudged == a else (a if nudged == b else b)
        dist = r.get("distance_mm")
        return f"Nudged {nudged.replace('_', ' ')} away from {other.replace('_', ' ')} by {int(dist)} mm to resolve overlap (within room)."
    return f"Could not nudge {a.replace('_', ' ')} and {b.replace('_', ' ')} apart: {r.get('reason', 'nudge would place object outside room')}"


def _build_summary(
    valid: bool,
    violation_count: int,
    nudge_applied: int,
    nudge_failed: int,
) -> str:
    """One-line clear summary of what happened."""
    if valid and nudge_applied == 0 and nudge_failed == 0:
        return "Layout is valid. No overlaps and no rule violations."
    if valid and (nudge_applied > 0 or nudge_failed > 0):
        parts = ["Layout is valid after overlap resolution."]
        if nudge_applied > 0:
            parts.append(f"{nudge_applied} overlap(s) were fixed by moving objects (nudge).")
        if nudge_failed > 0:
            parts.append(f"{nudge_failed} overlap(s) could not be fixed (nudge failed, constrained by walls).")
        return " ".join(parts)
    parts = [f"Layout has {violation_count} rule violation(s)."]
    if nudge_applied > 0:
        parts.append(f"{nudge_applied} overlap(s) were fixed by nudging.")
    if nudge_failed > 0:
        parts.append(f"{nudge_failed} overlap(s) could not be fixed.")
    return " ".join(parts)


def format_response(
    violations: list[dict[str, Any]],
    nudge_reports: list[dict[str, Any]] | None = None,
    nudge_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Build the full API response: raw violations + human-readable explanations,
    plus nudge summary and (optional) nudge error messages.
    """
    explanations = [format_violation_explanation(v) for v in violations]
    out = {
        "valid": len(violations) == 0,
        "violation_count": len(violations),
        "violations": violations,
        "explanations": explanations,
    }
    if nudge_reports:
        out["nudge_reports"] = nudge_reports
        out["nudge_explanations"] = [format_nudge_report(r) for r in nudge_reports]
    if nudge_errors:
        out["nudge_errors"] = nudge_errors
        out["nudge_error_explanations"] = [format_nudge_report(r) for r in nudge_errors]
    return out


# Describes what each field in the API response does (for clear response)
RESPONSE_GUIDE = {
    "summary": "One-line summary of the result: valid or not, how many violations, and what was done (e.g. nudging).",
    "valid": "True if the layout has no rule violations after nudging; false otherwise.",
    "violation_count": "Number of rule violations (from rules parsed from SHAPELY_LAYOUT_RULES.md: no overlap, clearance, proximity range, inside room).",
    "violations": "Machine-readable list of each violation (rule id, objects, distance, min/max required).",
    "explanations": "Human-readable text for each violation (what is wrong and what is required).",
    "nudge_applied": "Number of overlapping object pairs that were automatically separated (moved within the room).",
    "nudge_failed": "Number of overlapping pairs that could not be fixed (moving would go outside the room).",
    "nudge_reports": "Details of each nudge: which objects, which was moved, by how much, success or failure.",
    "nudge_explanations": "Human-readable lines for each nudge (what was moved and why).",
    "nudge_error_explanations": "Human-readable lines for each failed nudge (why it could not be fixed).",
    "space_evaluation": "For each violation: how far the object can be moved to fix it before hitting wall or another object.",
    "objects_found": "List of object names detected in the layout (including 'room' for the boundary).",
    "shapely_geometry": "Raw Shapely outputs for the uploaded layout (before nudge): object_pairs (distance_mm, intersects) and object_to_room (distance_to_boundary_mm, within_room). Overlaps show intersects: true here; rule validation runs on the layout after nudge.",
    "layout_after_nudge": "Updated room and object coordinates after any nudging (use this as the corrected layout).",
    "response_guide": "This object: explains what each key in the response means.",
}
