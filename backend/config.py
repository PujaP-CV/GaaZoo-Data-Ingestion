"""
GaaZoo Unified Configuration
All environment variables and settings for the combined app:
  - Image Pipeline (Amazon / Google SERP → Neo4j → Meshy 3D)
  - Design Personality Profile (Pinterest + Spotify + Image Upload → DPP)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Always load .env from the repo root regardless of where Python is invoked from
ROOT     = Path(__file__).resolve().parent.parent   # repo root
load_dotenv()
BACKEND  = Path(__file__).resolve().parent          # backend/
DATA_DIR      = ROOT / "backend" / "data"
DIR_2D        = DATA_DIR / "2d"
DIR_3D        = DATA_DIR / "3d"
DIR_3D_SCALED = DATA_DIR / "3d" / "scaled"
DIR_TEMP      = DATA_DIR / "temp"
DIR_DOLLHOUSE = DATA_DIR / "dollhouse"

DATA_DIR.mkdir(exist_ok=True)
DIR_2D.mkdir(exist_ok=True)
DIR_3D.mkdir(exist_ok=True)
DIR_3D_SCALED.mkdir(parents=True, exist_ok=True)
DIR_TEMP.mkdir(parents=True, exist_ok=True)
DIR_DOLLHOUSE.mkdir(parents=True, exist_ok=True)


# ── App config ─────────────────────────────────────────────────────────
class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    DEBUG      = os.getenv("DEBUG", "True") == "True"

    # ── Frontend URL ───────────────────────────────────────────────────
    # Where your frontend is hosted. Used in OAuth callback redirects.
    # Local dev  : http://localhost:3000  (python -m http.server 3000)
    # AWS S3     : http://gazoo-fe.s3-website-ap-southeast-2.amazonaws.com
    # Production : https://www.yourdomain.com
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")

    # ── Session cookies ────────────────────────────────────────────────
    # When frontend (S3/port 3000) and backend (port 8000) are on different
    # origins, browsers require SameSite=None + Secure (HTTPS) for cookies.
    # Set HTTPS=True in .env once you have SSL on the backend.
    _https = os.getenv("HTTPS", "False") == "True"
    SESSION_SAME_SITE = "none" if _https else "lax"
    SESSION_HTTPS_ONLY = _https
    SESSION_MAX_AGE   = 86400 * 30   # 30 days

    # ── Meshy AI (image → 3D) ──────────────────────────────────────────
    MESHY_API_KEY = os.getenv("MESHY_API_KEY", "").strip()
    MESHY_BASE    = os.getenv("MESHY_BASE", "https://api.meshy.ai/openapi/v1/image-to-3d").strip()

    # ── RapidAPI / Amazon ──────────────────────────────────────────────
    RAPIDAPI_KEY                 = os.getenv("RAPIDAPI_KEY", "").strip()
    RAPIDAPI_AMAZON_HOST         = os.getenv("RAPIDAPI_AMAZON_HOST", "real-time-amazon-data.p.rapidapi.com").strip()
    RAPIDAPI_AMAZON_SEARCH_PATH  = os.getenv("RAPIDAPI_AMAZON_SEARCH_PATH",  "search").strip()
    RAPIDAPI_AMAZON_PRODUCT_PATH = os.getenv("RAPIDAPI_AMAZON_PRODUCT_PATH", "product-details").strip()

    # ── Google SerpAPI ─────────────────────────────────────────────────
    SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()

    # ── Neo4j ──────────────────────────────────────────────────────────
    NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687").strip()
    NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j").strip()
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "").strip()

    # ── Catalog defaults ───────────────────────────────────────────────
    DEFAULT_CATALOG_NAME  = os.getenv("DEFAULT_CATALOG_NAME",  "GaaZoo Catalog").strip()
    DEFAULT_VENDOR_NAME   = os.getenv("DEFAULT_VENDOR_NAME",   "Amazon").strip()
    DEFAULT_VENDOR_DOMAIN = os.getenv("DEFAULT_VENDOR_DOMAIN", "amazon.com").strip()

    # ── Pinterest OAuth ────────────────────────────────────────────────
    # Pinterest only accepts 'localhost' redirects (not 127.0.0.1).
    # PINTEREST_FRONTEND_URL is where the backend redirects the browser
    # after a successful/failed Pinterest OAuth — must match the hostname
    # the user opened the frontend on (localhost:3000).
    PINTEREST_APP_ID       = os.getenv("PINTEREST_APP_ID",     "")
    PINTEREST_APP_SECRET   = os.getenv("PINTEREST_APP_SECRET", "")
    PINTEREST_REDIRECT_URI = os.getenv("PINTEREST_REDIRECT_URI", "http://localhost:8000/auth/pinterest/callback")
    PINTEREST_FRONTEND_URL = os.getenv("PINTEREST_FRONTEND_URL", "http://localhost:3000").rstrip("/")
    PINTEREST_SCOPE        = "boards:read,pins:read,user_accounts:read"
    PINTEREST_AUTH_URL     = "https://www.pinterest.com/oauth/"
    PINTEREST_TOKEN_URL    = "https://api.pinterest.com/v5/oauth/token"
    PINTEREST_API_BASE     = "https://api.pinterest.com/v5"

    # ── Spotify OAuth ──────────────────────────────────────────────────
    SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID",     "")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
    SPOTIFY_REDIRECT_URI  = os.getenv("SPOTIFY_REDIRECT_URI",  "http://localhost:8000/auth/spotify/callback")
    SPOTIFY_SCOPE         = "user-read-email user-read-private playlist-read-private playlist-read-collaborative"
    SPOTIFY_AUTH_URL      = "https://accounts.spotify.com/authorize"
    SPOTIFY_TOKEN_URL     = "https://accounts.spotify.com/api/token"

    # ── Google Gemini AI ───────────────────────────────────────────────
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL   = "gemini-2.5-flash"

    # ── ProcessIQ Vanilla Prompt API ───────────────────────────────────
    VANILLA_API_URL = "https://ac360.conceptvines.com/process-iq/template/vanilla_prompt_api"
    VANILLA_LLM     = os.getenv("VANILLA_LLM", "openai")   # openai | gemini | claude

    # Image question template:
    #   19 = fixed question "What drew you to this image?" + 4 AI-generated options
    #   20 = AI picks the most design-defining dimension, writes a focused question
    IMAGE_QUESTION_TEMPLATE_ID = int(os.getenv("IMAGE_QUESTION_TEMPLATE_ID", "19"))

    # Interior design scene + objects analysis (ProcessIQ Excel template):
    #   Analyse one room image → JSON with scene (style, palette, lighting) and objects (furniture, flooring, decor, etc.) with dimensions, materials, colors, finish.
    INTERIOR_DESIGN_ANALYSIS_TEMPLATE_ID = int(os.getenv("INTERIOR_DESIGN_ANALYSIS_TEMPLATE_ID", "28"))


# ── Module-level shortcuts (used by pipeline/module files) ─────────────
cfg = Config()

MESHY_API_KEY = cfg.MESHY_API_KEY
MESHY_BASE    = cfg.MESHY_BASE

RAPIDAPI_KEY                 = cfg.RAPIDAPI_KEY
RAPIDAPI_AMAZON_HOST         = cfg.RAPIDAPI_AMAZON_HOST
RAPIDAPI_AMAZON_SEARCH_PATH  = cfg.RAPIDAPI_AMAZON_SEARCH_PATH
RAPIDAPI_AMAZON_PRODUCT_PATH = cfg.RAPIDAPI_AMAZON_PRODUCT_PATH

SERPAPI_KEY = cfg.SERPAPI_KEY

NEO4J_URI      = cfg.NEO4J_URI
NEO4J_USER     = cfg.NEO4J_USER
NEO4J_PASSWORD = cfg.NEO4J_PASSWORD

DEFAULT_CATALOG_NAME  = cfg.DEFAULT_CATALOG_NAME
DEFAULT_VENDOR_NAME   = cfg.DEFAULT_VENDOR_NAME
DEFAULT_VENDOR_DOMAIN = cfg.DEFAULT_VENDOR_DOMAIN
