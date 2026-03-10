"""RapidAPI Real-Time Amazon Data: search, product details, download images."""
import json
import re
import requests
from pathlib import Path
from typing import List, Optional, Dict, Any

from config import (
    RAPIDAPI_KEY,
    RAPIDAPI_AMAZON_HOST,
    RAPIDAPI_AMAZON_SEARCH_PATH,
    RAPIDAPI_AMAZON_PRODUCT_PATH,
    DIR_2D,
)


def _headers() -> dict:
    return {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_AMAZON_HOST,
    }


def search_products(
    query: str,
    country: str = "US",
    page: int = 1,
) -> List[Dict[str, Any]]:
    """
    Search Amazon products. Returns list of product dicts with at least:
    asin, title, product_photo (url), and whatever else the API returns.
    """
    if not RAPIDAPI_KEY:
        raise ValueError("RAPIDAPI_KEY is not set in .env")
    url = "https://{}/{}".format(RAPIDAPI_AMAZON_HOST, RAPIDAPI_AMAZON_SEARCH_PATH.strip("/"))
    params = {"query": query, "country": country, "page": page}
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Handle different response shapes
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "data" in data and "products" in data["data"]:
            return data["data"]["products"]
        if "data" in data and isinstance(data["data"], list):
            return data["data"]
        if "products" in data:
            return data["products"]
        if "results" in data:
            return data["results"]
    return []


def product_details(asin: str, country: str = "US") -> Optional[Dict[str, Any]]:
    """Get product details by ASIN (more images and metadata)."""
    if not RAPIDAPI_KEY:
        raise ValueError("RAPIDAPI_KEY is not set in .env")
    url = "https://{}/{}".format(RAPIDAPI_AMAZON_HOST, RAPIDAPI_AMAZON_PRODUCT_PATH.strip("/"))
    params = {"asin": asin, "country": country}
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "data" in data:
        return data.get("data") or data.get("data", {}).get("product")
    return data if isinstance(data, dict) else None


# Country code -> Amazon TLD for product page URL
_AMAZON_COUNTRY_TLD = {
    "US": "amazon.com",
    "IN": "amazon.in",
    "UK": "amazon.co.uk",
    "GB": "amazon.co.uk",
    "DE": "amazon.de",
    "FR": "amazon.fr",
    "IT": "amazon.it",
    "ES": "amazon.es",
    "CA": "amazon.ca",
    "AU": "amazon.com.au",
    "JP": "amazon.co.jp",
    "BR": "amazon.com.br",
    "MX": "amazon.com.mx",
}


def _amazon_product_url(asin: str, country: str = "US") -> str:
    """Build the actual product page URL on Amazon (not the image URL)."""
    if not asin or not asin.strip():
        return ""
    asin = asin.strip()
    country = (country or "US").upper()
    tld = _AMAZON_COUNTRY_TLD.get(country) or "amazon.com"
    return f"https://www.{tld}/dp/{asin}"


def _normalize_product(p: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize product dict to our schema: asin, title, image_urls, category, style, color, texture."""
    asin = (p.get("asin") or p.get("product_id") or "").strip()
    title = (
        p.get("product_title")
        or p.get("title")
        or p.get("name")
        or p.get("product_title")
        or ""
    )
    # Collect all image URLs for gallery (main first, then product_photos/images)
    photo = p.get("product_photo") or p.get("main_image") or p.get("image") or p.get("thumbnail")
    all_images = p.get("product_photos") or p.get("images") or p.get("image_list") or []
    image_urls = []
    if photo and isinstance(photo, str) and photo.startswith("http"):
        image_urls.append(photo)
    seen = {u.rstrip("/") for u in image_urls}
    for entry in all_images:
        url = None
        if isinstance(entry, str) and entry.startswith("http"):
            url = entry
        elif isinstance(entry, dict):
            url = entry.get("link") or entry.get("url") or entry.get("href") or entry.get("large") or entry.get("medium")
        if url and url.rstrip("/") not in seen:
            seen.add(url.rstrip("/"))
            image_urls.append(url)
    category = p.get("category") or p.get("product_category") or ""
    if isinstance(category, dict):
        category = category.get("name") or category.get("title") or json.dumps(category)
    # Pull from item_attributes / attributes / product_details (common in Amazon APIs)
    attrs = _extract_attributes(p)
    # Metadata: prefer API attributes, then inferred
    style = p.get("style") or attrs.get("style") or attrs.get("Style") or ""
    texture = p.get("texture") or attrs.get("texture") or attrs.get("Texture") or ""
    color = (
        p.get("color") or attrs.get("color") or attrs.get("Color")
        or _infer_color_from_title(title)
    )
    material = p.get("material") or attrs.get("material") or attrs.get("Material") or ""
    width, height = _parse_dimensions(p)
    product_dimensions = _parse_product_dimensions(p, attrs)
    if isinstance(style, (dict, list)):
        style = json.dumps(style) if style else ""
    if isinstance(texture, (dict, list)):
        texture = json.dumps(texture) if texture else ""
    if isinstance(color, (dict, list)):
        color = json.dumps(color) if color else _infer_color_from_title(title)
    if isinstance(material, (dict, list)):
        material = json.dumps(material) if material else ""
    primary_image = image_urls[0] if image_urls else ""

    return {
        "asin": asin,
        "title": title,
        "image_urls": [u for u in image_urls if u],
        "image_url": primary_image,
        "category": category,
        "style": style,
        "texture": texture,
        "color": color,
        "material": material,
        "width": width,
        "height": height,
        "product_dimensions": product_dimensions,
        "raw": p,
    }


def _extract_attributes(p: Dict[str, Any]) -> Dict[str, str]:
    """Extract color, material, style, etc. from item_attributes, attributes, product_details."""
    out = {}
    for key in ("item_attributes", "attributes", "product_details", "ProductDetails"):
        attrs = p.get(key)
        if not isinstance(attrs, dict):
            continue
        for k, v in attrs.items():
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            if isinstance(v, dict):
                v = v.get("value") or v.get("display_value") or next(iter(v.values()), "")
            elif isinstance(v, list) and v:
                v = v[0]
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
    return out


def _parse_dimensions(p: Dict[str, Any]) -> tuple:
    """Try to get image width/height (pixels) from product dict. Returns (width, height) or (None, None)."""
    w, h = p.get("image_width"), p.get("image_height")
    if w is not None and h is not None:
        try:
            return int(w), int(h)
        except (TypeError, ValueError):
            pass
    dims = p.get("dimensions")  # some APIs use "dimensions" for image size
    if isinstance(dims, dict):
        w = dims.get("width") or dims.get("image_width")
        h = dims.get("height") or dims.get("image_height")
        if w is not None and h is not None:
            try:
                return int(float(w)), int(float(h))
            except (TypeError, ValueError):
                pass
    return None, None


def _parse_product_dimensions(p: Dict[str, Any], attrs: Dict[str, str]) -> Optional[str]:
    """
    Extract physical object dimensions (e.g. "50 x 30 x 25 inches") from API metadata.
    Used for product dimensions from Amazon item_attributes / product_dimensions.
    """
    # From extracted attributes (Product Dimensions, Item Dimensions, etc.)
    for key in (
        "Product Dimensions", "Item Dimensions", "product_dimensions", "item_dimensions",
        "Product dimensions", "Item dimensions", "Dimensions",
    ):
        val = attrs.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # Top-level string
    for key in ("product_dimensions", "item_dimensions"):
        val = p.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Dict: format as "L x W x H" or "W x H"
    dims = p.get("product_dimensions") or p.get("item_dimensions")
    if isinstance(dims, dict):
        parts = []
        for k in ("length", "width", "height", "depth"):
            v = dims.get(k) or dims.get(k.capitalize())
            if v is not None and str(v).strip():
                parts.append(str(v).strip())
        if parts:
            unit = dims.get("unit") or dims.get("Unit") or ""
            if unit:
                return " x ".join(parts) + " " + str(unit).strip()
            return " x ".join(parts)
    return None


def _infer_color_from_title(title: str) -> str:
    """Infer color from product title if possible."""
    if not title:
        return ""
    title_lower = title.lower()
    colors = (
        "black", "white", "red", "blue", "green", "gray", "grey", "brown",
        "silver", "gold", "navy", "beige", "wood", "chrome", "stainless",
    )
    for c in colors:
        if c in title_lower:
            return c.capitalize()
    return ""


def download_images(asin: str, image_urls: List[str], max_images: int = 5) -> List[str]:
    """
    Download images for an ASIN into data/2d/{asin}/. Returns list of local paths.
    """
    asin_dir = DIR_2D / _safe_filename(asin)
    asin_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, url in enumerate(image_urls[:max_images]):
        if not url or not url.startswith("http"):
            continue
        try:
            r = requests.get(url, timeout=15, stream=True)
            r.raise_for_status()
            ext = _ext_from_content_type(r.headers.get("Content-Type", "")) or ".jpg"
            path = asin_dir / f"image_{i}{ext}"
            path.write_bytes(r.content)
            paths.append(str(path))
        except Exception:
            continue
    return paths


def _safe_filename(s: str) -> str:
    return re.sub(r"[^\w\-.]", "_", s)[:64]


def _ext_from_content_type(ct: str) -> str:
    if "png" in ct:
        return ".png"
    if "gif" in ct:
        return ".gif"
    if "webp" in ct:
        return ".webp"
    return ".jpg"


def fetch_and_prepare_product(
    query: str,
    country: str = "US",
    max_products: int = 5,
    max_images_per_product: int = 1,
) -> List[Dict[str, Any]]:
    """
    Search Amazon, get product details for each result, download images,
    and return list of normalized items ready for catalog upsert.
    """
    products = search_products(query, country=country)
    results = []
    for p in products[:max_products]:
        asin = p.get("asin") or p.get("product_id")
        if not asin:
            continue
        details = product_details(asin, country=country)
        if details:
            merged = {**p, **details}
        else:
            merged = p
        norm = _normalize_product(merged)
        if not norm["image_urls"]:
            continue
        # Actual product page URL on Amazon (not the image URL) for Source link and dimension scraping
        norm["source_url"] = _amazon_product_url(norm["asin"], country)
        local_paths = download_images(
            norm["asin"], norm["image_urls"], max_images=max_images_per_product
        )
        if not local_paths:
            continue
        norm["image_paths"] = local_paths
        norm["image_path_used"] = local_paths[0]  # first image for 3D
        # If API didn't give dimensions, read from downloaded file
        if (norm.get("width") is None or norm.get("height") is None) and local_paths:
            try:
                from image_utils import get_image_dimensions
                w, h = get_image_dimensions(local_paths[0])
                if w and h:
                    norm["width"], norm["height"] = w, h
            except Exception:
                pass
        # Always compute base64 for the primary local image
        try:
            from image_utils import get_image_base64
            norm["image_base64"] = get_image_base64(local_paths[0])
        except Exception:
            norm["image_base64"] = None
        results.append(norm)
    return results