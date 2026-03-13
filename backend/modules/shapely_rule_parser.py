"""
Shapely Layout Demo — Rule Parser

Reads SHAPELY_LAYOUT_RULES.md and extracts structured rules for the rule engine.
Parses the actual markdown format: -   R4 Circulation: clearance around furniture ≥ 600 mm
"""

import re
from pathlib import Path
from typing import Any

# Default path relative to backend
DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "SHAPELY_LAYOUT_RULES.md"

# Implementable types the engine can evaluate
IMPLEMENTABLE_TYPES = ("no_overlap", "min_clearance", "proximity_range", "inside_room")


def _normalize_object_name(name: str) -> str:
    """e.g. 'coffee table' -> 'coffee_table'"""
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _parse_description(description: str) -> dict[str, Any]:
    """Extract type, min_mm, max_mm, object_a, object_b from rule description text."""
    desc = description.strip().lower()
    out: dict[str, Any] = {}

    # min_mm: "≥ 600 mm" or "≥600 mm" or first number in "350--450 mm"
    m_ge = re.search(r"≥\s*(\d+)\s*mm", desc)
    if m_ge:
        out["min_mm"] = int(m_ge.group(1))
    m_range = re.search(r"(\d+)\s*--\s*(\d+)\s*mm", desc)
    if m_range:
        out["min_mm"] = int(m_range.group(1))
        out["max_mm"] = int(m_range.group(2))

    # max_mm: only from range; "≤ 450 mm" could be added if needed
    if "max_mm" not in out:
        m_le = re.search(r"≤\s*(\d+)\s*mm", desc)
        if m_le:
            out["max_mm"] = int(m_le.group(1))

    # type and object_a, object_b from keywords
    if "must not overlap" in desc or "no overlap" in desc or "not overlap" in desc:
        out["type"] = "no_overlap"
        return out
    if "inside room" in desc or "inside room boundary" in desc or "objects inside" in desc:
        out["type"] = "inside_room"
        return out
    if "clearance" in desc and ("≥" in description or "minimum" in desc or m_ge or "around furniture" in desc):
        out["type"] = "min_clearance"
        if "min_mm" not in out and m_range:
            out["min_mm"] = int(m_range.group(1))  # use range min as fallback
        if "min_mm" not in out:
            out["min_mm"] = 600  # default from common rule
        return out
    # "coffee table distance to sofa 350--450 mm" or "X distance to Y"
    if "distance to" in desc or "distance" in desc and m_range:
        out["type"] = "proximity_range"
        # Try to parse "X distance to Y" or "X ... Y ... mm"
        parts = re.split(r"\s+distance\s+to\s+", desc, maxsplit=1)
        if len(parts) == 2:
            left = parts[0].strip()
            right = parts[1].strip()
            # right may end with "350--450 mm"
            right = re.sub(r"\s*\d+\s*--\s*\d+\s*mm\s*$", "", right).strip()
            if left:
                out["object_a"] = _normalize_object_name(left)
            if right:
                out["object_b"] = _normalize_object_name(right)
        if "min_mm" not in out and m_range:
            out["min_mm"] = int(m_range.group(1))
            out["max_mm"] = int(m_range.group(2))
        return out

    out["type"] = "unsupported"
    return out


def _dedupe_key(rule: dict[str, Any]) -> tuple:
    """
    Key for deduplication. For min_clearance we keep only one rule (first seen, typically R4 600 mm)
    so the same layout is not reported multiple times for R4, R5, R7, etc.
    For other types we deduplicate by (type, min_mm, max_mm, object_a, object_b).
    """
    rtype = rule.get("type")
    if rtype == "min_clearance":
        return ("min_clearance",)  # only one min_clearance rule (first in file)
    return (
        rtype,
        rule.get("min_mm"),
        rule.get("max_mm"),
        rule.get("object_a"),
        rule.get("object_b"),
    )


def parse_rules_file(rules_path: Path | str | None = None) -> list[dict[str, Any]]:
    """
    Parse the markdown rules file (actual format: -   R4 Circulation: clearance around furniture ≥ 600 mm).
    Returns a list of rule dicts with rule_id, type, description, min_mm, max_mm, object_a, object_b.
    Deduplicates by (type, min_mm, max_mm, object_a, object_b) and keeps first rule_id.
    Only returns rules with implementable types (no_overlap, min_clearance, proximity_range, inside_room).
    """
    path = Path(rules_path) if rules_path else DEFAULT_RULES_PATH
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    # Match: -   R4 Circulation: ... or -   R0 Baseline: ... or -   R00 Baseline: ...
    line_re = re.compile(r"^\s*-\s+R(\d+)\s+[\w\s]+:\s*(.+)$")
    seen_keys: set[tuple] = set()
    rules: list[dict[str, Any]] = []

    for line in text.splitlines():
        m = line_re.match(line.strip())
        if not m:
            continue
        rule_id_num = m.group(1)
        rule_id = f"R{rule_id_num}"
        description = m.group(2).strip()
        parsed = _parse_description(description)
        rule_type = parsed.get("type", "unsupported")
        if rule_type not in IMPLEMENTABLE_TYPES:
            continue
        rule = {
            "rule_id": rule_id,
            "type": rule_type,
            "description": description,
            **{k: v for k, v in parsed.items() if k != "type"},
        }
        key = _dedupe_key(rule)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        rules.append(rule)

    return rules
