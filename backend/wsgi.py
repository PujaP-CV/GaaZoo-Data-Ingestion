"""
WSGI/ASGI entry point for production.

With uvicorn (recommended):
  uvicorn wsgi:app --host 0.0.0.0 --port 8000 --workers 4

With gunicorn + uvicorn workers:
  gunicorn wsgi:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000
"""

from app import create_app

app = application = create_app()
