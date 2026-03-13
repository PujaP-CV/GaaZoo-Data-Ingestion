# Using GaaZoo with Ngrok (one tunnel)

You only need **one** ngrok tunnel. The backend serves the frontend at `/` and `/dpp`, so tunnelling port 8000 exposes both the app and the API.

## Steps

### 1. Run one tunnel

```bash
ngrok http 8000
```

Use the HTTPS URL ngrok shows (e.g. `https://maxine-stonable-floggingly.ngrok-free.dev`).

### 2. Start the backend

```bash
cd backend
uvicorn app:app --reload --port 8000
```

### 3. Backend `.env`

Set your **single** ngrok URL everywhere (replace with your actual ngrok URL if different):

```env
FRONTEND_URL=https://YOUR-NGROK-URL
PINTEREST_FRONTEND_URL=https://YOUR-NGROK-URL

SPOTIFY_REDIRECT_URI=https://YOUR-NGROK-URL/auth/spotify/callback
PINTEREST_REDIRECT_URI=https://YOUR-NGROK-URL/auth/pinterest/callback

HTTPS=True
```

No need to change `frontend/config.js` — when the page is opened from an ngrok hostname, the frontend uses the same origin for API calls.

### 4. Developer dashboards

- **Spotify**: [Dashboard](https://developer.spotify.com/dashboard) → your app → Redirect URIs → add  
  `https://YOUR-NGROK-URL/auth/spotify/callback`
- **Pinterest**: [Developer portal](https://developers.pinterest.com/) → your app → Redirect URIs → add  
  `https://YOUR-NGROK-URL/auth/pinterest/callback`

### 5. Open the app

Open **the ngrok URL** in the browser (e.g. `https://maxine-stonable-floggingly.ngrok-free.dev/`). The same URL serves the UI and the API; Pinterest and Spotify OAuth will work.

---

**Local (no ngrok):** Use `http://127.0.0.1:8000/` (backend serves frontend) or run the frontend on port 3000 and set redirect URIs to `http://127.0.0.1:8000/...` and `http://localhost:8000/...` in `.env` and dashboards. Set `HTTPS=False` or leave it unset.
