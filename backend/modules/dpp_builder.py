"""
modules/dpp_builder.py
-----------------------
Builds the Design Personality Profile (DPP) from Pinterest board/pin data.
Extracts style signals, keywords, and constructs the profile JSON.

In production this would call Vision AI per pin image.
For this demo we use keyword analysis on titles/descriptions + board names.
"""

import re
from collections import Counter


# ── Style keyword taxonomy ───────────────────────────────────────────────────

STYLE_KEYWORDS = {
    "Minimalist": [
        "minimal", "minimalist", "clean", "simple", "uncluttered",
        "white", "monochrome", "sparse", "zen", "void", "stripped"
    ],
    "Japandi": [
        "japandi", "wabi-sabi", "wabi sabi", "japanese", "nordic",
        "scandi", "scandinavian", "hygge", "neutral", "serene", "muted"
    ],
    "Mid-Century Modern": [
        "mid-century", "midcentury", "retro", "eames", "60s", "1960",
        "walnut", "teak", "atomic", "vintage modern", "tapered leg"
    ],
    "Organic Modern": [
        "organic", "natural", "earthy", "biophilic", "curved",
        "terracotta", "clay", "stone", "linen", "jute", "rattan", "woven"
    ],
    "Industrial": [
        "industrial", "loft", "concrete", "exposed brick", "steel",
        "raw", "factory", "urban", "pipe", "metal", "warehouse"
    ],
    "Maximalist": [
        "maximalist", "eclectic", "bold", "layered", "pattern",
        "colorful", "rich", "opulent", "gallery wall", "collected"
    ],
    "Luxury": [
        "luxury", "marble", "gold", "brass", "velvet", "silk",
        "high-end", "designer", "premium", "refined", "bespoke"
    ],
    "Bohemian": [
        "boho", "bohemian", "eclectic", "folk", "global", "macrame",
        "plants", "textile", "fringe", "festival", "free spirit"
    ],
    "Farmhouse": [
        "farmhouse", "rustic", "shiplap", "barn", "country",
        "cotton", "linen", "distressed", "reclaimed", "wood grain"
    ],
    "Contemporary": [
        "contemporary", "modern", "sleek", "current", "clean lines",
        "glass", "chrome", "geometric", "angular", "monochromatic"
    ],
}

MATERIAL_KEYWORDS = {
    "natural_wood":     ["wood", "oak", "walnut", "teak", "timber", "wooden", "pine", "mahogany"],
    "stone_marble":     ["marble", "stone", "granite", "terrazzo", "travertine", "slate"],
    "metal_brass":      ["brass", "metal", "steel", "iron", "copper", "bronze", "gold"],
    "linen_fabric":     ["linen", "fabric", "textile", "cotton", "velvet", "upholstered", "woven"],
    "concrete":         ["concrete", "cement", "plaster", "lime wash"],
    "glass_acrylic":    ["glass", "acrylic", "transparent", "mirror"],
    "rattan_cane":      ["rattan", "cane", "bamboo", "wicker", "jute"],
}

COLOR_MOOD_KEYWORDS = {
    "warm_neutral":  ["warm", "sand", "beige", "cream", "terracotta", "rust", "amber"],
    "cool_neutral":  ["cool", "grey", "gray", "white", "silver", "mist", "ash"],
    "dark_moody":    ["dark", "black", "charcoal", "navy", "deep", "moody", "dramatic"],
    "earthy":        ["earthy", "brown", "olive", "sage", "green", "clay", "mushroom"],
    "light_airy":    ["light", "bright", "airy", "fresh", "pale", "blush", "ivory"],
}


# ── Public builder function ──────────────────────────────────────────────────

def build_dpp_from_pinterest(boards: list[dict]) -> dict:
    """
    Build a Design Personality Profile from Pinterest boards + pins.

    Args:
        boards: List of board dicts (from pinterest_fetcher), each with nested pins.

    Returns:
        DPP dict with all 6 dimensions populated from analysis.
    """
    # Collect all text: board names + pin titles + descriptions
    all_text = _collect_all_text(boards)

    # Analyse each dimension
    style_scores  = _score_keywords(all_text, STYLE_KEYWORDS)
    material_hits = _score_keywords(all_text, MATERIAL_KEYWORDS)
    color_hits    = _score_keywords(all_text, COLOR_MOOD_KEYWORDS)

    # Build identity archetypes (top 3 styles)
    identity = _build_identity(style_scores)

    # Build material preferences (top 3)
    materials = _top_n(material_hits, n=3)

    # Infer color mood
    color_mood = _top_n(color_hits, n=1)
    color_mood_label = color_mood[0] if color_mood else "warm_neutral"

    # Build the ai_prompt_injection string
    prompt_injection = _build_prompt_injection(identity, materials, color_mood_label, boards)

    return {
        "profile_version": "v1.0",
        "source": "pinterest",
        "boards_analyzed": len(boards),
        "pins_analyzed": sum(len(b.get("pins", [])) for b in boards),

        "1_identity": {
            "archetypes": identity,
            "confidence": _calc_confidence(style_scores),
        },

        "2_colour_preference": {
            "mood": color_mood_label,
            "detected_from": "board and pin keyword analysis",
        },

        "3_material_preference": {
            "preferred_materials": materials,
        },

        # 4, 5, 6 require questionnaire — marked as pending
        "4_finish_preference":     {"status": "pending_questionnaire"},
        "5_pattern_tolerance":     {"status": "pending_questionnaire"},
        "6_furniture_permanence":  {"status": "pending_questionnaire"},

        "ai_prompt_injection": prompt_injection,

        "board_summary": _summarise_boards(boards),
    }


# ── Private helpers ──────────────────────────────────────────────────────────

def _collect_all_text(boards: list[dict]) -> str:
    """Combine all board names, descriptions, pin titles and descriptions into one string."""
    parts = []
    for board in boards:
        parts.append(board.get("name", ""))
        parts.append(board.get("description", ""))
        for pin in board.get("pins", []):
            parts.append(pin.get("title", ""))
            parts.append(pin.get("description", ""))
    return " ".join(parts).lower()


def _score_keywords(text: str, taxonomy: dict) -> dict:
    """Score each category by counting keyword hits in text."""
    scores = {}
    for category, keywords in taxonomy.items():
        count = sum(text.count(kw.lower()) for kw in keywords)
        if count > 0:
            scores[category] = count
    return scores


def _top_n(scores: dict, n: int) -> list:
    """Return the top-n category names sorted by score."""
    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _ in sorted_items[:n]]


def _build_identity(style_scores: dict) -> dict:
    """
    Convert style scores into weighted archetypes (primary/secondary/tertiary).
    Weights always total 1.0.
    """
    top = _top_n(style_scores, n=3)

    if not top:
        return {
            "primary":   {"name": "Contemporary", "weight": 1.0},
            "secondary": None,
            "tertiary":  None,
        }

    # Simple weight distribution: 55 / 30 / 15
    weights = [0.55, 0.30, 0.15]
    keys    = ["primary", "secondary", "tertiary"]

    identity = {}
    for i, key in enumerate(keys):
        if i < len(top):
            identity[key] = {"name": top[i], "weight": weights[i]}
        else:
            identity[key] = None

    # Adjust weights if fewer than 3 styles detected
    if len(top) == 1:
        identity["primary"]["weight"] = 1.0
    elif len(top) == 2:
        identity["primary"]["weight"]   = 0.65
        identity["secondary"]["weight"] = 0.35

    return identity


def _calc_confidence(scores: dict) -> float:
    """Rough confidence score based on how many distinct signals were found."""
    total_hits = sum(scores.values())
    if total_hits == 0:
        return 0.1
    elif total_hits < 5:
        return 0.35
    elif total_hits < 15:
        return 0.60
    elif total_hits < 30:
        return 0.75
    else:
        return 0.90


def _build_prompt_injection(
    identity: dict,
    materials: list,
    color_mood: str,
    boards: list[dict]
) -> str:
    """Build the AI prompt injection string from profile dimensions."""
    primary = identity.get("primary", {})
    secondary = identity.get("secondary")
    tertiary  = identity.get("tertiary")

    style_desc = primary.get("name", "Contemporary")
    if secondary:
        style_desc += f" with {secondary['name']} secondary"
    if tertiary:
        style_desc += f" and {tertiary['name']} influence"

    mat_desc = ", ".join(materials) if materials else "natural materials"

    color_map = {
        "warm_neutral": "warm neutral tones (sand, beige, cream)",
        "cool_neutral": "cool neutral tones (grey, white, silver)",
        "dark_moody":   "dark moody tones (charcoal, navy, black)",
        "earthy":       "earthy tones (olive, sage, terracotta, brown)",
        "light_airy":   "light airy tones (pale, blush, ivory, white)",
    }
    color_desc = color_map.get(color_mood, "balanced neutral palette")

    board_names = ", ".join(b["name"] for b in boards[:3] if b.get("name"))

    return (
        f"User's design style: {style_desc}. "
        f"Preferred materials: {mat_desc}. "
        f"Color palette: {color_desc}. "
        f"Inspiration boards include: {board_names}. "
        f"Suggest real purchasable furniture and decor that matches this aesthetic."
    )


def _summarise_boards(boards: list[dict]) -> list[dict]:
    """Create a lightweight summary of each board for the API response."""
    return [
        {
            "name":      b.get("name", ""),
            "pin_count": len(b.get("pins", [])),
            "image_url": b.get("image_url", ""),
        }
        for b in boards
    ]


# ════════════════════════════════════════════════════════════════════════════
# IMAGE UPLOAD — DPP Builder
# Builds DPP from Gemini Vision image analysis results
# ════════════════════════════════════════════════════════════════════════════

def build_dpp_from_images(analyses: list[dict]) -> dict:
    """
    Build a DPP from Gemini Vision image analysis results.

    Args:
        analyses: Output list from image_analyser.analyse_images()

    Returns:
        Full DPP dict — same structure as build_dpp_from_pinterest().
    """
    valid   = [a for a in analyses if not a.get("error") and a.get("confidence", 0) >= 0.3]
    failed  = len(analyses) - len(valid)

    if not valid:
        return _image_empty_profile(failed)

    # Aggregate signals across all valid images
    all_styles    = []
    all_materials = []
    all_colours   = []
    all_moods     = []
    densities     = []

    for a in valid:
        all_styles    += a.get("styles", [])
        all_materials += a.get("materials", [])
        # dominant_colours may be [{hex, label}] objects or plain hex strings
        for c in a.get("dominant_colours", []):
            if isinstance(c, dict) and c.get("hex"):
                all_colours.append({"hex": c["hex"], "label": c.get("label", "")})
            elif isinstance(c, str) and c:
                all_colours.append({"hex": c, "label": ""})
        all_moods     += a.get("mood_tags", [])
        if a.get("spatial_density") and a["spatial_density"] != "unknown":
            densities.append(a["spatial_density"])

    # Weight style votes by per-image confidence so high-confidence images
    # contribute more to the identity than low-confidence ones.
    style_scores: Counter = Counter()
    for a in valid:
        img_conf = float(a.get("confidence", 0.5) or 0.5)
        weight = round(img_conf * 10)  # e.g. 0.95 → 10 votes, 0.45 → 5 votes
        for s in a.get("styles", []):
            style_scores[s] += max(weight, 1)

    mat_scores    = Counter(all_materials)

    identity    = _image_build_identity(style_scores)
    materials   = [m for m, _ in mat_scores.most_common(3)]
    colour_mood = _image_infer_colour_mood(all_moods)
    density     = Counter(densities).most_common(1)[0][0] if densities else "moderate"
    confidence  = _image_calc_confidence(len(valid))

    prompt = _image_build_prompt(identity, materials, colour_mood, all_moods)

    return {
        "profile_version":  "v1.0",
        "source":           "image_upload",
        "images_analyzed":  len(valid),
        "images_failed":    failed,

        "1_identity": {
            "archetypes": identity,
            "confidence": confidence,
        },
        "2_colour_preference": {
            "mood":            colour_mood,
            "sampled_colours": _dedup_colours(all_colours)[:6],
        },
        "3_material_preference": {
            "preferred_materials": materials,
        },
        "4_finish_preference":    {"status": "pending_questionnaire"},
        "5_pattern_tolerance":    {"status": "pending_questionnaire"},
        "6_furniture_permanence": {"status": "pending_questionnaire"},

        "mood_tags":       list(dict.fromkeys(all_moods))[:6],
        "spatial_density": density,

        "ai_prompt_injection": prompt,

        "board_summary": [
            {
                "name":      a["filename"],
                "pin_count": 0,
                "image_url": "",
            }
            for a in valid
        ],
    }


def _dedup_colours(colours: list) -> list:
    """Deduplicate colour list (of {hex, label} dicts) preserving first-seen label per hex."""
    seen = {}
    for c in colours:
        hex_val = c.get("hex", "").upper()
        if hex_val and hex_val not in seen:
            seen[hex_val] = c.get("label", "")
    return [{"hex": h, "label": l} for h, l in seen.items()]


def _image_build_identity(style_counts: Counter) -> dict:
    top = [s for s, _ in style_counts.most_common(3)]
    if not top:
        return {
            "primary":   {"name": "Contemporary", "weight": 1.0},
            "secondary": None,
            "tertiary":  None,
        }
    weights = {1: [1.0, 0, 0], 2: [0.65, 0.35, 0], 3: [0.55, 0.30, 0.15]}
    w = weights.get(len(top), [0.55, 0.30, 0.15])
    keys = ["primary", "secondary", "tertiary"]
    return {
        keys[i]: {"name": top[i], "weight": w[i]} if i < len(top) else None
        for i in range(3)
    }


def _image_infer_colour_mood(moods: list) -> str:
    text = " ".join(moods).lower()
    if any(w in text for w in ["dark", "moody", "dramatic", "deep", "noir"]):
        return "dark_moody"
    if any(w in text for w in ["bright", "airy", "light", "fresh", "breezy"]):
        return "light_airy"
    if any(w in text for w in ["earthy", "rustic", "organic", "natural"]):
        return "earthy"
    if any(w in text for w in ["cool", "crisp", "minimal", "clean"]):
        return "cool_neutral"
    return "warm_neutral"


def _image_calc_confidence(count: int) -> float:
    return min(0.95, {1: 0.45, 2: 0.60, 3: 0.70, 4: 0.78}.get(count, 0.85))


def _image_build_prompt(identity: dict, materials: list, colour_mood: str, moods: list) -> str:
    primary   = identity.get("primary", {})
    secondary = identity.get("secondary")
    tertiary  = identity.get("tertiary")
    style = primary.get("name", "Contemporary")
    if secondary: style += f" with {secondary['name']} secondary"
    if tertiary:  style += f" and {tertiary['name']} touches"
    mat   = ", ".join(materials) if materials else "natural materials"
    colour_map = {
        "warm_neutral": "warm neutrals (sand, cream, terracotta)",
        "cool_neutral": "cool neutrals (grey, white, silver)",
        "dark_moody":   "dark moody tones (charcoal, navy, forest)",
        "light_airy":   "light airy tones (blush, ivory, pale)",
        "earthy":       "earthy tones (olive, clay, brown)",
    }
    colour   = colour_map.get(colour_mood, "balanced neutral palette")
    mood_str = ", ".join(list(dict.fromkeys(moods))[:4]) if moods else "calm and considered"
    return (
        f"Design style: {style}. "
        f"Preferred materials: {mat}. "
        f"Colour palette: {colour}. "
        f"Overall mood: {mood_str}. "
        f"Only suggest real purchasable furniture matching this aesthetic."
    )


def _image_empty_profile(failed: int) -> dict:
    return {
        "profile_version":       "v1.0",
        "source":                "image_upload",
        "images_analyzed":       0,
        "images_failed":         failed,
        "1_identity":            {"archetypes": {"primary": {"name": "Contemporary", "weight": 1.0}, "secondary": None, "tertiary": None}, "confidence": 0.1},
        "2_colour_preference":   {"mood": "warm_neutral", "sampled_colours": [], "preferred_colours": []},
        "3_material_preference": {"preferred_materials": []},
        "4_finish_preference":   {"status": "pending_questionnaire"},
        "5_pattern_tolerance":   {"status": "pending_questionnaire"},
        "6_furniture_permanence":{"status": "pending_questionnaire"},
        "mood_tags":             [],
        "spatial_density":       "moderate",
        "ai_prompt_injection":   "No profile data — all images failed analysis.",
        "board_summary":         [],
    }