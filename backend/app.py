"""
GaaZoo — Unified Flask Application
Combines three modules into one server on port 5000:
  1. Design Personality Profile  → /auth/*, /profile/*, /ai/*
  2. Data Ingestion Pipeline     → /api/fetch-images, /api/catalog/*
  3. 3D Catalog Viewer           → /generate-3d, /proxy-glb, /api/convert-*
"""

from flask import Flask, jsonify, request
from flask_cors import CORS

from config import Config, DATA_DIR, DIR_2D, DIR_3D
from modules.catalog_db import init_db

# ── Blueprints ─────────────────────────────────────────────────────────
from routes.auth_routes    import auth_bp
from routes.profile_routes import profile_bp
from routes.ai_routes      import ai_bp
from routes.catalog_routes import catalog_bp
from routes.viewer_routes  import viewer_bp


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config.from_object(Config)

    CORS(app, supports_credentials=True, origins=["http://localhost:5000", "http://127.0.0.1:5000"])

    # ── Neo4j lazy init — skip for OAuth callbacks and static serving ──
    @app.before_request
    def _ensure_db():
        skip_prefixes = ("/", "/auth", "/profile", "/ai", "/health", "/.well-known")
        if any(request.path == p or request.path.startswith(p + "/") for p in skip_prefixes):
            return
        if request.path == "/" or request.path.startswith("/static"):
            return
        try:
            init_db()
        except Exception as e:
            try:
                from neo4j.exceptions import AuthError, ServiceUnavailable
                if isinstance(e, AuthError):
                    return jsonify({"error": "Neo4j authentication failed. Check NEO4J_USER and NEO4J_PASSWORD in .env"}), 503
                if isinstance(e, ServiceUnavailable):
                    return jsonify({"error": "Neo4j is not reachable. Start Neo4j and ensure NEO4J_URI is correct in .env"}), 503
            except ImportError:
                pass
            raise

    # ── Register blueprints ────────────────────────────────────────────
    app.register_blueprint(auth_bp,    url_prefix="/auth")
    app.register_blueprint(profile_bp, url_prefix="/profile")
    app.register_blueprint(ai_bp,      url_prefix="/ai")
    app.register_blueprint(catalog_bp)   # /api/catalog/*, /api/fetch-images, /api/add-local-vendor, /api/convert-*
    app.register_blueprint(viewer_bp)    # /, /proxy-glb, /generate-3d, /api/files/*

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "service": "GaaZoo Unified API"})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
