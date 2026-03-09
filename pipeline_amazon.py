"""
Amazon ingestion pipeline.

Flow:
  RapidAPI (Amazon) → download images → infer type/subtype
  → upsert :Image nodes into Neo4j graph catalog
"""

from typing import List, Dict, Any

from config import RAPIDAPI_KEY, DEFAULT_VENDOR_NAME, DEFAULT_VENDOR_DOMAIN
from catalog_db import init_db, upsert_item
from amazon_client import fetch_and_prepare_product


# ── Category inference ────────────────────────────────────────────────

_SUBTYPE_KEYWORDS = {
    "sofa":       ("Furniture",   "Sofa"),
    "couch":      ("Furniture",   "Sofa"),
    "chair":      ("Furniture",   "Chair"),
    "table":      ("Furniture",   "Table"),
    "desk":       ("Furniture",   "Desk"),
    "bed":        ("Furniture",   "Bed"),
    "wardrobe":   ("Furniture",   "Wardrobe"),
    "shelf":      ("Furniture",   "Shelf"),
    "shelves":    ("Furniture",   "Shelf"),
    "lamp":       ("Lighting",    "Lamp"),
    "light":      ("Lighting",    "Light Fixture"),
    "rug":        ("Decor",       "Rug"),
    "curtain":    ("Decor",       "Curtain"),
    "mirror":     ("Decor",       "Mirror"),
    "mouse":      ("Electronics", "Mouse"),
    "keyboard":   ("Electronics", "Keyboard"),
    "monitor":    ("Electronics", "Monitor"),
    "laptop":     ("Electronics", "Laptop"),
    "headphone":  ("Electronics", "Headphone"),
    "speaker":    ("Electronics", "Speaker"),
}

_CATEGORY_TO_TYPE = {
    "furniture":   "Furniture",
    "home":        "Home & Living",
    "lighting":    "Lighting",
    "decor":       "Decor",
    "electronics": "Electronics",
    "kitchen":     "Kitchen",
    "office":      "Office",
    "outdoor":     "Outdoor",
    "sports":      "Sports",
    "clothing":    "Clothing",
    "toys":        "Toys",
}


def _infer_type_and_subtype(title: str, category: str) -> tuple:
    """Returns (product_type, product_subtype)."""
    tl = (title or "").lower()
    for kw, (ptype, psub) in _SUBTYPE_KEYWORDS.items():
        if kw in tl:
            return ptype, psub
    cat_lower = (category or "").lower()
    for ck, ptype in _CATEGORY_TO_TYPE.items():
        if ck in cat_lower:
            return ptype, "Other"
    return "General", "Other"


# ── Main pipeline ─────────────────────────────────────────────────────

def run_amazon_pipeline(
    query: str,
    country: str = "US",
    max_products: int = 5,
    max_images_per_product: int = 3,
    vendor_name:   str = DEFAULT_VENDOR_NAME,
    vendor_domain: str = DEFAULT_VENDOR_DOMAIN,
) -> List[Dict[str, Any]]:
    """
    End-to-end: search Amazon → download images → upsert into Neo4j.
    Returns list of { asin, title, product_type, product_subtype, vendor }.
    """
    if not RAPIDAPI_KEY:
        raise ValueError("RAPIDAPI_KEY is not set in .env")

    init_db()

    items = fetch_and_prepare_product(
        query=query,
        country=country,
        max_products=max_products,
        max_images_per_product=max_images_per_product,
    )

    result = []
    for it in items:
        title    = it.get("title") or ""
        category = it.get("category") or ""
        product_type, product_subtype = _infer_type_and_subtype(title, category)

        asin = upsert_item(
            asin=it["asin"],
            title=title,
            vendor_name=vendor_name,
            vendor_domain=vendor_domain,
            product_type=product_type,
            product_subtype=product_subtype,
            image_paths=it.get("image_paths"),
            image_path_used=it.get("image_path_used"),
            image_url=it.get("image_url"),
            source_url=it.get("source_url"),  # actual product page URL (e.g. amazon.in/dp/ASIN), not image URL
            query=query,
            style=it.get("style"),
            colour=it.get("color") or it.get("colour"),
            texture=it.get("texture"),
            material=it.get("material"),
            width=it.get("width"),
            height=it.get("height"),
            product_dimensions=it.get("product_dimensions"),
            image_base64=it.get("image_base64"),
            conversion_status="pending",
            raw_metadata=it.get("raw"),
        )

        result.append({
            "asin":            asin,
            "title":           title,
            "product_type":    product_type,
            "product_subtype": product_subtype,
            "vendor":          vendor_name,
        })

    return result