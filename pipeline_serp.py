"""
Google SerpAPI ingestion pipeline.

Flow:
  SerpAPI (Google Images) → download images → infer type/subtype
  → upsert :Image nodes into Neo4j graph catalog

Usage (CLI):
  python run_pipeline.py fetch-serp "modern sofa" --num 10

Usage (API):
  POST /api/fetch-serp
  { "query": "modern sofa", "num": 10, "vendor_name": "Google Images" }
"""

from typing import List, Dict, Any

from config import SERPAPI_KEY, DEFAULT_CATALOG_NAME
from catalog_db import init_db, upsert_item
from serp_client import fetch_and_prepare_serp


# ── Category inference (same logic as pipeline_amazon) ───────────────

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


def _infer_type_and_subtype(title: str, query: str) -> tuple:
    """
    Check title keywords first, then fall back to the search query itself.
    Returns (product_type, product_subtype).
    """
    for text in (title, query):
        tl = (text or "").lower()
        for kw, (ptype, psub) in _SUBTYPE_KEYWORDS.items():
            if kw in tl:
                return ptype, psub
    return "General", "Other"


# ── Main pipeline ─────────────────────────────────────────────────────

def run_serp_pipeline(
    query: str,
    num: int = 10,
    max_images_per_result: int = 1,
    country: str = "us",
    vendor_name:   str = "Google Images",
    vendor_domain: str = "google.com",
    catalog_name:  str = DEFAULT_CATALOG_NAME,
) -> List[Dict[str, Any]]:
    """
    End-to-end: search Google Images → download one image per query → upsert into Neo4j.
    Returns list of { asin, title, product_type, product_subtype, vendor } dicts.
    """
    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY is not set in .env")

    init_db()

    items = fetch_and_prepare_serp(
        query=query,
        num=num,
        max_images_per_result=max_images_per_result,
        max_images_per_product=1,
        country=country,
    )

    result = []
    for it in items:
        title = it.get("title") or query
        product_type, product_subtype = _infer_type_and_subtype(title, query)

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
            source_url=it.get("source_url"),
            query=query,
            colour=it.get("colour"),
            style=it.get("style"),
            texture=it.get("texture"),
            material=it.get("material"),
            width=it.get("width"),
            height=it.get("height"),
            product_dimensions=it.get("product_dimensions"),  # scraped from source_url in serp_client
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
            "image_url":       it.get("image_url"),
            "source_domain":   it.get("source_domain"),
        })

    return result