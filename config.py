"""App configuration and paths."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent

# ── Meshy ──────────────────────────────────────────────────────────────
MESHY_API_KEY = os.environ.get("MESHY_API_KEY", "").strip()
MESHY_BASE    = os.environ.get("BASE", "https://api.meshy.ai/openapi/v1/image-to-3d").strip()

# ── RapidAPI / Amazon ──────────────────────────────────────────────────
RAPIDAPI_KEY                 = os.environ.get("RAPIDAPI_KEY", "").strip()
RAPIDAPI_AMAZON_HOST         = os.environ.get("RAPIDAPI_AMAZON_HOST", "real-time-amazon-data.p.rapidapi.com").strip()
RAPIDAPI_AMAZON_SEARCH_PATH  = os.environ.get("RAPIDAPI_AMAZON_SEARCH_PATH",  "search").strip()
RAPIDAPI_AMAZON_PRODUCT_PATH = os.environ.get("RAPIDAPI_AMAZON_PRODUCT_PATH", "product-details").strip()

# ── Google SerpAPI ────────────────────────────────────────────────────
# Get your key at https://serpapi.com/
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()

# ── Neo4j ──────────────────────────────────────────────────────────────
NEO4J_URI      = os.environ.get("NEO4J_URI",      "bolt://localhost:7687").strip()
NEO4J_USER     = os.environ.get("NEO4J_USER",     "neo4j").strip()
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "").strip()

# ── Default graph seed values ──────────────────────────────────────────
DEFAULT_CATALOG_NAME  = os.environ.get("DEFAULT_CATALOG_NAME",  "GaaZoo Catalog").strip()
DEFAULT_VENDOR_NAME   = os.environ.get("DEFAULT_VENDOR_NAME",   "Amazon").strip()
DEFAULT_VENDOR_DOMAIN = os.environ.get("DEFAULT_VENDOR_DOMAIN", "amazon.com").strip()

# ── Local storage ──────────────────────────────────────────────────────
DATA_DIR = ROOT / "data"
DIR_2D   = DATA_DIR / "2d"
DIR_3D   = DATA_DIR / "3d"

DATA_DIR.mkdir(exist_ok=True)
DIR_2D.mkdir(exist_ok=True)
DIR_3D.mkdir(exist_ok=True)