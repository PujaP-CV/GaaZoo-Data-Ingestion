"""
Google SerpAPI — image search client.

Uses the SerpAPI /search endpoint with engine=google_images.
Each result is normalised to the same shape as amazon_client so
pipeline_serp can call catalog_db.upsert_item() with identical args.

For each result, source_url is the actual product page (from "link"), not the image URL.
That source can be any site (Amazon, Wayfair, IKEA, etc.). We scrape it to extract
product_dimensions (e.g. "46D x 51W x 92H Centimeters") and store them in the catalog.

SerpAPI docs: https://serpapi.com/images-results
Free tier:    100 searches/month
"""

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

from config import SERPAPI_KEY, DIR_2D

SERPAPI_ENDPOINT = "https://serpapi.com/search"


# ── Search ────────────────────────────────────────────────────────────

def search_images(
    query: str,
    num: int = 10,
    safe: str = "active",       # "active" | "off"
    country: str = "us",        # gl param
    language: str = "en",       # hl param
) -> List[Dict[str, Any]]:
    """
    Search Google Images via SerpAPI.
    Returns list of raw result dicts (each has original, thumbnail, title, source, link …).
    """
    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY is not set in .env")

    params = {
        "engine":  "google_images",
        "q":       query,
        "num":     num,
        "safe":    safe,
        "gl":      country,
        "hl":      language,
        "api_key": SERPAPI_KEY,
    }
    resp = requests.get(SERPAPI_ENDPOINT, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # SerpAPI wraps image results under "images_results"
    return data.get("images_results", [])


# ── Normalise ─────────────────────────────────────────────────────────

def _infer_color_from_title(title: str) -> str:
    if not title:
        return ""
    tl = title.lower()
    for c in ("black","white","red","blue","green","gray","grey","brown",
              "silver","gold","navy","beige","cream","yellow","pink","orange"):
        if c in tl:
            return c.capitalize()
    return ""


def _is_likely_image_url(url: str) -> bool:
    """True if URL looks like a direct image file (not a product page)."""
    if not url or not isinstance(url, str):
        return False
    u = url.lower().split("?")[0]
    return any(u.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"))


def normalize_serp_result(r: Dict[str, Any], query: str) -> Dict[str, Any]:
    """
    Convert one SerpAPI images_result entry into our standard item shape.
    SerpAPI: "link" = page providing the image (product URL), "original" = image URL.
    We keep source_url as the actual product/source page URL, never the image URL.
    """
    # Image URL only — do not use "link" here (link is the product page)
    original_url = (r.get("original") or "").strip()
    thumbnail_url = (r.get("thumbnail") or "").strip()
    title = (r.get("title") or r.get("snippet") or query).strip()
    # Product/source page URL only — "link" is the page; "source" is domain name (e.g. "Amazon.com"), not a URL
    link = (r.get("link") or "").strip()
    if link and _is_likely_image_url(link):
        link = ""  # API sometimes returns image URL in link; don't use as product page
    source_url = link
    source_domain = (r.get("source") or "").strip() or _domain_from_url(source_url)

    # Stable image_id: prefer original image URL, else link + title so we still have a unique id
    id_input = original_url or f"{link}_{title}" or "serp_fallback"
    image_id = "serp_" + hashlib.sha1(id_input.encode()).hexdigest()[:16]

    colour = _infer_color_from_title(title)
    width = r.get("original_width") or r.get("width") or r.get("thumbnail_width")
    height = r.get("original_height") or r.get("height") or r.get("thumbnail_height")
    if width is not None and height is not None:
        try:
            width, height = int(width), int(height)
        except (TypeError, ValueError):
            width, height = None, None
    else:
        width, height = None, None

    return {
        "image_id":     image_id,
        "asin":         image_id,           # used as unique key in Neo4j
        "title":        title,
        "image_url":    original_url,       # stored in Neo4j — NOT base64
        "thumbnail_url":thumbnail_url,
        "source_url":   source_url,
        "source_domain":source_domain,
        "query":        query,
        "colour":       colour,
        "style":        "",
        "texture":      "",
        "material":     "",
        "width":        width,
        "height":       height,
        "image_urls":   [original_url] if original_url else ([thumbnail_url] if thumbnail_url else []),
        "raw":          r,
    }


def _domain_from_url(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url)
    return m.group(1) if m else ""


# ── Download ──────────────────────────────────────────────────────────

def download_serp_images(
    image_id: str,
    image_urls: List[str],
    max_images: int = 1,
) -> List[str]:
    """
    Download images into data/2d/<image_id>/. Returns list of local paths.
    Falls back gracefully — skips URLs that fail (many original images block hotlinking).
    """
    safe_id  = re.sub(r"[^\w\-.]", "_", image_id)[:64]
    out_dir  = DIR_2D / safe_id
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for i, url in enumerate(image_urls[:max_images]):
        if not url or not url.startswith("http"):
            continue
        try:
            r = requests.get(
                url, timeout=15, stream=True,
                headers={"User-Agent": "Mozilla/5.0"},   # some servers block Python UA
            )
            r.raise_for_status()
            ct  = r.headers.get("Content-Type", "")
            ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
            path = out_dir / f"image_{i}{ext}"
            path.write_bytes(r.content)
            paths.append(str(path))
        except Exception:
            continue

    return paths


# Browser-like headers so image hosts are less likely to block
_SERP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Headers for fetching HTML pages (product details) — request as browser so we get full page
_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def download_serp_one_per_result(
    image_id: str,
    result_url_pairs: List[tuple],
    max_images: int = 5,
) -> List[str]:
    """
    Download exactly one image per SERP result (different angles/images).
    Tries thumbnail first (SerpAPI/cached URLs usually work), then original.
    Returns list of local paths, so no duplicate same-image.
    """
    safe_id = re.sub(r"[^\w\-.]", "_", image_id)[:64]
    out_dir = DIR_2D / safe_id
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (orig, thumb) in enumerate(result_url_pairs[:max_images]):
        if i >= max_images:
            break
        # Try thumbnail first (SerpAPI-hosted or gstatic often allow; originals often block)
        for url in (thumb, orig):
            if not url or not isinstance(url, str) or not url.startswith("http"):
                continue
            # SerpAPI-hosted image URLs often require the API key for access
            if "serpapi.com" in url and SERPAPI_KEY:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}api_key={SERPAPI_KEY}"
            try:
                time.sleep(0.25)  # avoid rate limits
                r = requests.get(
                    url, timeout=20, stream=True,
                    headers=_SERP_HEADERS,
                )
                r.raise_for_status()
                data = r.content
                if not data or len(data) < 100:
                    continue
                ct = r.headers.get("Content-Type", "")
                ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
                path = out_dir / f"image_{i}{ext}"
                path.write_bytes(data)
                paths.append(str(path))
                break
            except Exception:
                continue
    return paths


def fetch_product_image_urls_from_page(
    page_url: str,
    max_images: int = 8,
    timeout: int = 15,
) -> List[str]:
    """
    Fetch a product page (e.g. from SERP source_url) and extract product/gallery image URLs.
    Returns list of absolute image URLs, preferring same-domain and likely product images.
    """
    if not page_url or not page_url.startswith("http"):
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    try:
        r = requests.get(
            page_url,
            timeout=timeout,
            headers=_PAGE_HEADERS,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    base_domain = urlparse(page_url).netloc.lower()
    seen: set = set()
    urls: List[str] = []
    # Common selectors for product galleries
    for img in soup.find_all("img", src=True):
        src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
        if not src or not src.strip():
            continue
        src = src.strip()
        if src.startswith("//"):
            src = "https:" + src
        elif not src.startswith("http"):
            src = urljoin(page_url, src)
        if not src.startswith("http"):
            continue
        # Skip tiny or non-product images
        skip = False
        for part in ("logo", "icon", "avatar", "pixel", "1x1", "spacer", "banner", "ad."):
            if part in src.lower():
                skip = True
                break
        width = img.get("width") or img.get("data-width")
        if width is not None:
            try:
                if int(width) < 80:
                    skip = True
            except (TypeError, ValueError):
                pass
        if skip or src in seen:
            continue
        seen.add(src)
        urls.append(src)
        if len(urls) >= max_images:
            break
    return urls[:max_images]


def _extract_product_image_urls_from_soup(soup: Any, page_url: str, max_images: int = 8) -> List[str]:
    """Extract product/gallery image URLs from already-fetched soup. Same logic as fetch_product_image_urls_from_page."""
    if not page_url or not page_url.startswith("http"):
        return []
    seen: set = set()
    urls: List[str] = []
    for img in soup.find_all("img", src=True):
        src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
        if not src or not src.strip():
            continue
        src = src.strip()
        if src.startswith("//"):
            src = "https:" + src
        elif not src.startswith("http"):
            src = urljoin(page_url, src)
        if not src.startswith("http"):
            continue
        skip = False
        for part in ("logo", "icon", "avatar", "pixel", "1x1", "spacer", "banner", "ad."):
            if part in src.lower():
                skip = True
                break
        width = img.get("width") or img.get("data-width")
        if width is not None:
            try:
                if int(width) < 80:
                    skip = True
            except (TypeError, ValueError):
                pass
        if skip or src in seen:
            continue
        seen.add(src)
        urls.append(src)
        if len(urls) >= max_images:
            break
    return urls[:max_images]


# Max time to wait for dimension scrape so the main flow never hangs (source site may block or be slow)
_DIMENSION_FETCH_WAIT_SEC = 12
_dimension_executor: Optional[ThreadPoolExecutor] = None


def _fetch_product_details_with_timeout(page_url: str) -> Dict[str, Optional[str]]:
    """Run fetch_product_details_from_page in a thread; return dict with product_dimensions and material (or None values) if it takes longer than _DIMENSION_FETCH_WAIT_SEC."""
    global _dimension_executor
    if _dimension_executor is None:
        _dimension_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dim_fetch")
    future = _dimension_executor.submit(fetch_product_details_from_page, page_url)
    try:
        return future.result(timeout=_DIMENSION_FETCH_WAIT_SEC)
    except (FuturesTimeoutError, Exception):
        return {"title": None, "product_dimensions": None, "material": None, "colour": None, "image_urls": []}


def _extract_material_from_page(soup: Any, text: str, raw_html: str) -> Optional[str]:
    """Extract material from parsed page (tables, JSON-LD, meta, label:value). Same approach as dimensions."""
    import json
    # Tables: Material / Item Material row
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            first_text = (cells[0].get_text() or "").strip().lower()
            if "material" in first_text or first_text in ("item material", "product material"):
                val = (cells[1].get_text() or "").strip()
                if val and 1 < len(val) < 120:
                    return re.sub(r"\s+", " ", val)
    for row in soup.find_all("tr"):
        ths, tds = row.find_all("th"), row.find_all("td")
        if not ths or not tds:
            continue
        if "material" in (ths[0].get_text() or "").lower():
            val = (tds[0].get_text() or "").strip()
            if val and 1 < len(val) < 120:
                return re.sub(r"\s+", " ", val)
    # JSON-LD Product
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            if isinstance(data, dict):
                data = [data]
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict) or "Product" not in str(item.get("@type") or ""):
                    continue
                mat = item.get("material")
                if mat and isinstance(mat, str) and mat.strip():
                    return mat.strip()
                if isinstance(mat, list) and mat:
                    return str(mat[0]).strip() if str(mat[0]).strip() else None
        except (json.JSONDecodeError, TypeError):
            continue
    # Meta tags
    for meta in soup.find_all("meta", attrs={"property": True}) + soup.find_all("meta", attrs={"name": True}):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if "material" in prop:
            content = meta.get("content")
            if content and isinstance(content, str) and content.strip():
                return content.strip()
    # Raw HTML / text: "Material: Wood", "Material - Sheesham"
    for label in ("Material", "Product Material", "Item Material"):
        m = re.search(
            re.escape(label) + r"\s*[:\-]\s*([^\n<]{2,80})",
            raw_html,
            re.IGNORECASE,
        )
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if val and not val.startswith("http") and len(val) < 80:
                return val
    m = re.search(r"(?:Product\s+)?[Mm]aterial\s*[:\-]\s*([^\n|]+?)(?:\s*\||\n|$)", text)
    if m and m.group(1) and len(m.group(1).strip()) > 1:
        return m.group(1).strip()
    return None


def _extract_color_from_page(soup: Any, text: str, raw_html: str) -> Optional[str]:
    """Extract color/colour from parsed page (tables, JSON-LD, meta, label:value). Same approach as material."""
    import json
    # Tables: Color / Colour / Item Color row
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            first_text = (cells[0].get_text() or "").strip().lower()
            if first_text in ("color", "colour") or "color" in first_text or "colour" in first_text:
                val = (cells[1].get_text() or "").strip()
                if val and 1 < len(val) < 80:
                    return re.sub(r"\s+", " ", val)
    for row in soup.find_all("tr"):
        ths, tds = row.find_all("th"), row.find_all("td")
        if not ths or not tds:
            continue
        lt = (ths[0].get_text() or "").lower()
        if "color" in lt or "colour" in lt:
            val = (tds[0].get_text() or "").strip()
            if val and 1 < len(val) < 80:
                return re.sub(r"\s+", " ", val)
    # JSON-LD Product
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            if isinstance(data, dict):
                data = [data]
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict) or "Product" not in str(item.get("@type") or ""):
                    continue
                col = item.get("color") or item.get("colour")
                if col and isinstance(col, str) and col.strip():
                    return col.strip()
                if isinstance(col, list) and col:
                    return str(col[0]).strip() if str(col[0]).strip() else None
        except (json.JSONDecodeError, TypeError):
            continue
    # Meta tags
    for meta in soup.find_all("meta", attrs={"property": True}) + soup.find_all("meta", attrs={"name": True}):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if "color" in prop or "colour" in prop:
            content = meta.get("content")
            if content and isinstance(content, str) and content.strip():
                return content.strip()
    # Raw HTML / text: "Color: Green", "Colour - Black"
    for label in ("Color", "Colour", "Item Color", "Product Color", "Finish"):
        m = re.search(
            re.escape(label) + r"\s*[:\-]\s*([^\n<]{1,60})",
            raw_html,
            re.IGNORECASE,
        )
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if val and not val.startswith("http") and len(val) < 60:
                return val
    m = re.search(r"(?:Product\s+)?[Cc]olou?r\s*[:\-]\s*([^\n|]+?)(?:\s*\||\n|$)", text)
    if m and m.group(1) and len(m.group(1).strip()) > 0:
        return m.group(1).strip()
    return None


def _extract_title_from_page(soup: Any, raw_html: str) -> Optional[str]:
    """Extract product title from parsed page (JSON-LD Product name, og:title, title tag, h1)."""
    import json
    # JSON-LD Product name (best for e-commerce)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            if isinstance(data, dict):
                data = [data]
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict) or "Product" not in str(item.get("@type") or ""):
                    continue
                name = item.get("name")
                if name and isinstance(name, str) and len(name.strip()) > 2:
                    return name.strip()
        except (json.JSONDecodeError, TypeError):
            continue
    # og:title
    for meta in soup.find_all("meta", attrs={"property": True}):
        if (meta.get("property") or "").lower() == "og:title":
            content = meta.get("content")
            if content and isinstance(content, str) and len(content.strip()) > 2:
                return content.strip()
            break
    # <title>
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        t = title_tag.string.strip()
        if t and len(t) > 2 and not t.startswith("http"):
            return t
    # First h1 (common for product pages)
    h1 = soup.find("h1")
    if h1:
        t = (h1.get_text() or "").strip()
        if t and len(t) > 2 and len(t) < 300:
            return t
    return None


def fetch_product_details_from_page(
    page_url: str,
    timeout: int = 8,
    max_images_from_page: int = 8,
) -> Dict[str, Any]:
    """
    Scrape the given product page and extract product title, dimensions, material, colour, and image URLs.
    Returns {"title", "product_dimensions", "material", "colour", "image_urls"} (values may be None or []).
    """
    out = {"title": None, "product_dimensions": None, "material": None, "colour": None, "image_urls": []}
    if not page_url or not page_url.startswith("http"):
        return out
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return out
    headers = dict(_PAGE_HEADERS)
    try:
        parsed = urlparse(page_url)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        pass
    try:
        r = requests.get(page_url, timeout=timeout, headers=headers)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        raw_html = r.text
    except (requests.exceptions.Timeout, requests.exceptions.RequestException, Exception):
        return out

    out["title"] = _extract_title_from_page(soup, raw_html)
    out["product_dimensions"] = _extract_dimensions_from_page(soup, text, raw_html)
    out["material"] = _extract_material_from_page(soup, text, raw_html)
    out["colour"] = _extract_color_from_page(soup, text, raw_html)
    out["image_urls"] = _extract_product_image_urls_from_soup(soup, page_url, max_images=max_images_from_page)
    return out


def _extract_dimensions_from_page(soup: Any, text: str, raw_html: str) -> Optional[str]:
    """Extract product dimensions from parsed page. Used by fetch_product_details_from_page."""
    import json
    # 0. E-commerce tables
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            first_text = (cells[0].get_text() or "").strip().lower()
            if "product dimension" in first_text or "item dimension" in first_text or first_text in ("dimensions", "size", "product size"):
                val = (cells[1].get_text() or "").strip()
                if val and 3 < len(val) < 150 and re.search(r"\d", val):
                    return re.sub(r"\s+", " ", val)
    for row in soup.find_all("tr"):
        ths, tds = row.find_all("th"), row.find_all("td")
        if not ths or not tds:
            continue
        if "dimension" in (ths[0].get_text() or "").lower() or (ths[0].get_text() or "").strip().lower() in ("dimensions", "size"):
            val = (tds[0].get_text() or "").strip()
            if val and 3 < len(val) < 150 and re.search(r"\d", val):
                return re.sub(r"\s+", " ", val)
    for label in ("Product Dimensions", "Item Dimensions", "Dimensions", "Product size", "Size"):
        m = re.search(
            re.escape(label) + r"</th\s*>[^<]*(?:<[^>]+>[^<]*)*?<td\s*[^>]*>([^<]+)",
            raw_html,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if val and 3 < len(val) < 150 and re.search(r"\d", val):
                return val
        m = re.search(
            re.escape(label) + r"\s*[:\-]\s*([^\n<]{5,120})",
            raw_html,
            re.IGNORECASE,
        )
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if val and re.search(r"\d", val) and not val.startswith("http"):
                return val
    m = re.search(
        r"(\d{1,4}\s*[DWHdwh]\s*[x×]\s*\d{1,4}\s*[DWHdwh]\s*[x×]\s*\d{1,4}\s*[DWHdwh]\s*(?:\s*Centimeters?|\s*Inches?|\s*cm|\s*inch|\s*mm|\s*m)\b)",
        raw_html,
        re.IGNORECASE,
    )
    if m:
        val = re.sub(r"\s+", " ", m.group(1)).strip()
        if 10 < len(val) < 100:
            return val

    # 1. JSON-LD Product
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                schema_type = item.get("@type") or ""
                if "Product" not in (schema_type if isinstance(schema_type, str) else " ".join(schema_type)):
                    continue
                w, h, d = item.get("width"), item.get("height"), item.get("depth")
                dims_str = item.get("dimensions") or item.get("productDimensions")
                if dims_str and isinstance(dims_str, str) and len(dims_str.strip()) > 2:
                    return dims_str.strip()
                parts = [str(x).strip() for x in (w, h, d) if x is not None]
                if len(parts) >= 2:
                    return " x ".join(parts)
        except (json.JSONDecodeError, TypeError):
            continue

    # 2. Meta tags
    for meta in soup.find_all("meta", attrs={"property": True}) + soup.find_all("meta", attrs={"name": True}):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if "dimension" in prop or (("product" in prop or "size" in prop) and "dimension" in prop):
            content = meta.get("content")
            if content and isinstance(content, str) and len(content.strip()) > 2:
                return content.strip()

    # 3. Generic tables, dl, label:value
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = (cells[0].get_text() or "").strip().lower()
            if "dimension" in label or ("product" in label and "size" in label) or label in ("size", "dimensions"):
                val = (cells[1].get_text() or "").strip()
                if val and 2 < len(val) < 150:
                    return val
    for dl in soup.find_all("dl"):
        dts, dds = dl.find_all("dt"), dl.find_all("dd")
        for i, dt in enumerate(dts):
            if i >= len(dds):
                break
            label = (dt.get_text() or "").strip().lower()
            if "dimension" in label or label in ("size", "dimensions"):
                val = (dds[i].get_text() or "").strip()
                if val and 2 < len(val) < 150:
                    return val
    for tag in soup.find_all(["div", "li", "span", "p"]):
        raw = (tag.get_text() or "").strip()
        if not raw or len(raw) > 200:
            continue
        if "dimension" not in raw.lower() and "size" not in raw.lower():
            continue
        for sep in (":", "-", "\u00a0"):
            if sep in raw:
                parts = raw.split(sep, 1)
                if len(parts) != 2:
                    continue
                if "dimension" in parts[0].lower() or parts[0].strip().lower() in ("size", "dimensions"):
                    val = parts[1].strip()
                    if val and 2 < len(val) < 120:
                        return val

    # 4. Text patterns
    for pattern in [
        r"(?:Product\s+)?[Dd]imensions?\s*[:\-]\s*([^\n|]+?)(?:\s*\||\n|$)",
        r"[Dd]imensions?\s*[:\-]\s*([^\n|]+?)(?:\s*\||\n|$)",
        r"(?:Item\s+)?[Ss]ize\s*[:\-]\s*([^\n|]+?)(?:\s*\||\n|$)",
    ]:
        m = re.search(pattern, text)
        if m and m.group(1) and len(m.group(1).strip()) > 3:
            return m.group(1).strip()

    # 5. Fallback regex
    for content in (text, raw_html):
        m = re.search(
            r"(\d{1,4}\s*[DWHdwh]\s*[x×]\s*\d{1,4}\s*[DWHdwh]\s*[x×]\s*\d{1,4}\s*[DWHdwh]\s*(?:Centimeters?|Inches?|cm|inch|in\.?|mm|m)\b)",
            content,
            re.IGNORECASE,
        )
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if 10 < len(val) < 100:
                return val
        m = re.search(
            r"(\d{1,4}\s*[x×]\s*\d{1,4}(?:\s*[x×]\s*\d{1,4})?)\s*(cm|inch|in\.?|mm|m|Centimeters?)\b",
            content,
            re.IGNORECASE,
        )
        if m:
            val = (m.group(1) + " " + m.group(2)).strip()
            if 5 < len(val) < 80:
                return val
        m = re.search(
            r"[Dd]imensions?[:\s\-]+(\d[\d\sx×\.\-]*(?:cm|inch|in|mm|m|Centimeters?)\b[^<>;]*)",
            content,
            re.IGNORECASE,
        )
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if 4 < len(val) < 100:
                return val
    return None


def download_serp_images_append(
    image_id: str,
    image_urls: List[str],
    start_index: int,
    max_images: int = 8,
) -> List[str]:
    """Download images into existing folder with indices start_index, start_index+1, ..."""
    if not image_urls or start_index < 0:
        return []
    safe_id = re.sub(r"[^\w\-.]", "_", image_id)[:64]
    out_dir = DIR_2D / safe_id
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, url in enumerate(image_urls[:max_images]):
        if not url or not isinstance(url, str) or not url.startswith("http"):
            continue
        try:
            time.sleep(0.2)
            r = requests.get(url, timeout=20, stream=True, headers=_SERP_HEADERS)
            r.raise_for_status()
            data = r.content
            if not data or len(data) < 100:
                continue
            ct = r.headers.get("Content-Type", "")
            ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
            idx = start_index + len(paths)
            path = out_dir / f"image_{idx}{ext}"
            path.write_bytes(data)
            paths.append(str(path))
        except Exception:
            continue
    return paths


# ── High-level fetch ──────────────────────────────────────────────────

def _query_slug(query: str) -> str:
    """Stable short slug from search query for folder/image_id."""
    slug = re.sub(r"[^\w\s-]", "", (query or "").strip().lower())
    slug = re.sub(r"[-\s]+", "_", slug).strip("_")[:32]
    return slug or "query"


def fetch_and_prepare_serp(
    query: str,
    num: int = 10,
    max_images_per_result: int = 1,
    max_images_per_product: Optional[int] = None,
    country: str = "us",
    group_as_single_product: bool = True,
) -> List[Dict[str, Any]]:
    """
    Search Google Images, download images, return list of normalised items
    ready for catalog_db.upsert_item().

    When group_as_single_product is True (default), returns one item per query
    with multiple image_paths (gallery). Uses max_images_per_product to cap
    how many different images (one per SERP result, original then thumbnail fallback).
    When False, returns one item per SERP result.
    """
    raw_results = search_images(query, num=num, country=country)
    raw_results = raw_results[:num]

    if group_as_single_product and raw_results:
        # One catalog "product" with a single image (first SERP result only)
        result_url_pairs = []
        first_norm = None
        for r in raw_results:
            n = normalize_serp_result(r, query)
            orig = (n["image_urls"] or [None])[0] or ""
            thumb = (
                (r.get("thumbnail") or r.get("thumbnail_link") or r.get("image")) or ""
            )
            if isinstance(thumb, str):
                thumb = thumb.strip()
            else:
                thumb = ""
            if orig or thumb:
                result_url_pairs.append((orig or thumb, thumb if orig else ""))
                if first_norm is None:
                    first_norm = n
                break
        if not result_url_pairs or first_norm is None:
            return []

        image_id = "serp_" + _query_slug(query) + "_" + hashlib.sha1(query.encode()).hexdigest()[:8]
        local_paths = download_serp_one_per_result(
            image_id, result_url_pairs, max_images=1
        )
        if not local_paths:
            return []

        norm = {
            "asin": image_id,
            "title": (first_norm.get("title") or query).strip(),
            "image_url": first_norm["image_url"],
            "source_url": first_norm.get("source_url", ""),
            "source_domain": first_norm.get("source_domain", ""),
            "query": query,
            "colour": first_norm.get("colour", ""),
            "style": first_norm.get("style", ""),
            "texture": first_norm.get("texture", ""),
            "material": first_norm.get("material", ""),
            "width": first_norm.get("width"),
            "height": first_norm.get("height"),
            "image_paths": local_paths,
            "image_path_used": local_paths[0],
            "raw": first_norm.get("raw"),
        }
        # Scrape source URL (actual product page) for product_dimensions, material, and multiple product images (max 12s wait)
        source_url = (first_norm.get("source_url") or "").strip()
        if source_url and source_url.startswith("http"):
            try:
                details = _fetch_product_details_with_timeout(source_url)
                if details.get("product_dimensions"):
                    norm["product_dimensions"] = details["product_dimensions"]
                if details.get("material"):
                    norm["material"] = details["material"]
                if details.get("colour"):
                    norm["colour"] = details["colour"]
                if details.get("title"):
                    norm["title"] = details["title"]
                # Download additional product images from source page and append to gallery
                extra_urls = details.get("image_urls") or []
                if extra_urls:
                    extra_paths = download_serp_images_append(
                        image_id, extra_urls, start_index=len(local_paths), max_images=6
                    )
                    if extra_paths:
                        local_paths = local_paths + extra_paths
                        norm["image_paths"] = local_paths
            except Exception:
                pass
        if norm.get("width") is None or norm.get("height") is None:
            try:
                from image_utils import get_image_dimensions
                w, h = get_image_dimensions(local_paths[0])
                if w and h:
                    norm["width"], norm["height"] = w, h
            except Exception:
                pass
        try:
            from image_utils import get_image_base64
            norm["image_base64"] = get_image_base64(local_paths[0])
        except Exception:
            norm["image_base64"] = None
        return [norm]

    # Legacy: one item per SERP result (each with one or few images)
    items = []
    for r in raw_results:
        norm = normalize_serp_result(r, query)
        if not norm["image_urls"]:
            continue

        local_paths = download_serp_images(
            norm["image_id"],
            norm["image_urls"],
            max_images=max_images_per_result,
        )
        norm["image_paths"]    = local_paths
        norm["image_path_used"] = local_paths[0] if local_paths else ""

        # Scrape source URL (actual product page) for product_dimensions, material, and multiple product images (max 12s wait)
        src = (norm.get("source_url") or "").strip()
        if src and src.startswith("http"):
            try:
                details = _fetch_product_details_with_timeout(src)
                if details.get("product_dimensions"):
                    norm["product_dimensions"] = details["product_dimensions"]
                if details.get("material"):
                    norm["material"] = details["material"]
                if details.get("colour"):
                    norm["colour"] = details["colour"]
                if details.get("title"):
                    norm["title"] = details["title"]
                extra_urls = details.get("image_urls") or []
                if extra_urls:
                    extra_paths = download_serp_images_append(
                        norm["image_id"], extra_urls, start_index=len(local_paths), max_images=6
                    )
                    if extra_paths:
                        local_paths = local_paths + extra_paths
                        norm["image_paths"] = local_paths
            except Exception:
                pass

        if (norm.get("width") is None or norm.get("height") is None) and local_paths:
            try:
                from image_utils import get_image_dimensions
                w, h = get_image_dimensions(local_paths[0])
                if w and h:
                    norm["width"], norm["height"] = w, h
            except Exception:
                pass

        try:
            from image_utils import get_image_base64
            norm["image_base64"] = get_image_base64(local_paths[0]) if local_paths else None
        except Exception:
            norm["image_base64"] = None
        items.append(norm)

    return items
