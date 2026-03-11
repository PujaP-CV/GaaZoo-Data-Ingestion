/**
 * GaaZoo Frontend Configuration
 * ─────────────────────────────
 * Set GAAZOO_API to your backend URL. This is the ONLY place to change it.
 *
 * Local dev  : 'http://127.0.0.1:8000'  (uvicorn app:app --port 8000)
 *   IMPORTANT: Use 127.0.0.1 — NOT localhost. Spotify/Pinterest only accept
 *   127.0.0.1 as a loopback redirect URI, and the session cookie must use
 *   the same hostname for login and callback or state-mismatch errors occur.
 *
 * AWS (EC2)  : 'http://13.54.3.159'     (Nginx proxies :80 → uvicorn :8000)
 * Production : 'https://api.yourdomain.com'
 *
 * Upload this file to S3 and update GAAZOO_API when the backend URL changes.
 * Never hardcode these URLs anywhere else — always use window.GAAZOO_API.
 *
 * OAuth redirect URIs to register in Spotify / Pinterest developer dashboards:
 *   Local : http://127.0.0.1:8000/auth/spotify/callback
 *           http://127.0.0.1:8000/auth/pinterest/callback
 *   AWS   : http://13.54.3.159/auth/spotify/callback
 *           http://13.54.3.159/auth/pinterest/callback
 *
 * Open the frontend at http://127.0.0.1:3000 (not localhost:3000).
 */
window.GAAZOO_API = "http://127.0.0.1:8000";

// Pinterest only accepts 'localhost' redirect URIs (not 127.0.0.1).
// Use this for Pinterest OAuth buttons only; use GAAZOO_API for everything else.
window.PINTEREST_API = "http://localhost:8000";
