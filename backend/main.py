"""
GaaZoo — Unified FastAPI Application
Framework: FastAPI (NOT Flask)

Three modules on one server (default port 8000):
  1. Design Personality Profile  → /auth/*, /profile/*, /ai/*
  2. Data Ingestion Pipeline     → /api/fetch-images, /api/catalog/*
  3. 3D Catalog Viewer           → /generate-3d, /proxy-glb, /api/convert-*

Run locally:
  cd backend
  uvicorn app:app --reload --port 8000

Frontend (separate):
  cd frontend
  python -m http.server 3000
"""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from config import Config, DATA_DIR, DIR_2D, DIR_3D
from modules.catalog_db import init_db

from routes.auth_routes    import router as auth_router
from routes.profile_routes import router as profile_router
from routes.ai_routes      import router as ai_router
from routes.catalog_routes import router as catalog_router
from routes.viewer_routes  import router as viewer_router
from routes.shapely_routes import router as shapely_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="GaaZoo Unified API",
        description="Design Personality Profile · Data Ingestion · 3D Catalog Viewer",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Session middleware ──────────────────────────────────────────────
    # Uses signed cookies (itsdangerous) — same mechanism as Flask sessions.
    # Added first so it runs closest to the route handlers (innermost).
    app.add_middleware(
        SessionMiddleware,
        secret_key=Config.SECRET_KEY,
        session_cookie="gaazoo_session",   # unique name avoids stale Flask cookies
        same_site=Config.SESSION_SAME_SITE,
        https_only=Config.SESSION_HTTPS_ONLY,
        max_age=Config.SESSION_MAX_AGE,
    )

    # ── CORS ────────────────────────────────────────────────────────────
    # Added second so it is outermost (handles preflight before session decoding).
    _default_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        Config.FRONTEND_URL,
    ]
    _extra = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    _origins = list(dict.fromkeys(_default_origins + _extra))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Neo4j lazy-init middleware ─────────────────────────────────────
    # Attempts to connect to Neo4j for catalog/pipeline routes.
    # If Neo4j is unavailable, the request proceeds — each catalog route
    # handles the missing DB gracefully (returns empty list / friendly error)
    # instead of blocking with a 503.
    _SKIP_DB = ("/", "/auth", "/profile", "/ai", "/health", "/.well-known",
                "/generate-3d", "/proxy-glb", "/scale-3d", "/3d-dimensions", "/dpp", "/dpp.html", "/docs", "/redoc",
                "/openapi.json", "/api/files", "/shapely")

    @app.middleware("http")
    async def ensure_db(request: Request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in _SKIP_DB):
            return await call_next(request)
        try:
            init_db()
        except Exception as e:
            # Log the warning but let the request continue — routes decide how to handle it
            logger.warning(f"Neo4j unavailable ({type(e).__name__}): {e}")
        return await call_next(request)

    # ── Routers ─────────────────────────────────────────────────────────
    app.include_router(auth_router,    prefix="/auth",    tags=["Auth"])
    app.include_router(profile_router, prefix="/profile", tags=["Profile"])
    app.include_router(ai_router,      prefix="/ai",      tags=["AI"])
    app.include_router(catalog_router,                    tags=["Catalog"])
    app.include_router(viewer_router,                     tags=["Viewer"])
    app.include_router(shapely_router,                    tags=["Shapely Demo"])

    # ── Health check ────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok", "service": "GaaZoo Unified API", "framework": "FastAPI"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, reload_excludes=["*.glb", "*.obj"])
