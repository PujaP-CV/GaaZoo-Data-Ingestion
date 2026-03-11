"""
Gunicorn configuration for production (uvicorn workers).

Usage:
  gunicorn wsgi:app -c gunicorn.conf.py

Or directly with uvicorn (simpler, no gunicorn needed):
  uvicorn wsgi:app --host 0.0.0.0 --port 8000 --workers 4
"""

import multiprocessing

# ── Binding ────────────────────────────────────────────────────────────
bind    = "0.0.0.0:8000"          # Nginx proxies :80 → :8000
# bind  = "unix:/tmp/gaazoo.sock" # Alternative: Unix socket for Nginx upstream

# ── Workers ────────────────────────────────────────────────────────────
worker_class = "uvicorn.workers.UvicornWorker"   # ASGI worker for FastAPI
workers      = multiprocessing.cpu_count() * 2 + 1

# ── Timeouts ───────────────────────────────────────────────────────────
timeout      = 360    # 6 min: covers long 3D-conversion polling loops
keepalive    = 5

# ── Logging ────────────────────────────────────────────────────────────
accesslog = "/var/log/gaazoo/access.log"
errorlog  = "/var/log/gaazoo/error.log"
loglevel  = "info"

# ── Process identity (EC2) ─────────────────────────────────────────────
# user  = "gaazoo"
# group = "gaazoo"
