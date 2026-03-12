/**
 * GaaZoo Frontend Configuration
 * ─────────────────────────────
 * Set GAAZOO_API to your backend URL. This is the ONLY place to change it.
 *
 * Local dev  : 'http://127.0.0.1:8000'  (uvicorn app:app --port 8000)
 *   Open frontend at http://127.0.0.1:3000 or run backend and open http://127.0.0.1:8000/
 *
 * Ngrok (ONE tunnel): Run "ngrok http 8000". Open the app at the ngrok URL.
 *   The backend serves the frontend at / and /dpp, so one URL does both.
 *   The frontend auto-uses the same origin for API when hostname contains "ngrok".
 *   In .env set SPOTIFY_REDIRECT_URI and PINTEREST_REDIRECT_URI to that URL + /auth/.../callback, and HTTPS=True.
 *
 * AWS (EC2)  : 'http://13.54.3.159'     (Nginx proxies :80 → uvicorn :8000)
 * Production : 'https://api.yourdomain.com'
 *
 * OAuth redirect URIs (Spotify / Pinterest dashboards):
 *   Local : http://127.0.0.1:8000/auth/spotify/callback, http://localhost:8000/auth/pinterest/callback
 *   Ngrok : https://YOUR-NGROK-URL/auth/spotify/callback, .../auth/pinterest/callback
 */
window.GAAZOO_API = "http://127.0.0.1:8000";

// Pinterest only accepts 'localhost' redirect URIs (not 127.0.0.1). Use for Pinterest OAuth only.
window.PINTEREST_API = "https://maxine-stonable-floggingly.ngrok-free.dev";

// When page is served from ngrok, use same origin for API (one tunnel to 8000 serves both frontend and backend).
(function () {
  if (typeof window === "undefined" || !window.location) return;
  if (window.location.hostname.indexOf("ngrok") !== -1) {
    var origin = window.location.origin.replace(/\/$/, "");
    window.GAAZOO_API = origin;
    window.PINTEREST_API = origin;
  }
})();
