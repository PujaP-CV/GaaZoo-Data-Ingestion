# GaaZoo — Unified Platform

A single Flask application combining three modules:

| Module | What it does |
|---|---|
| **Design Profile** | Build a Design Personality Profile (DPP) from Pinterest boards, Spotify playlists, or uploaded room images |
| **Data Ingestion** | Fetch product images from Amazon (RapidAPI) or Google Images (SerpAPI) → store in Neo4j catalog |
| **3D Catalog** | Browse catalog, convert product images to 3D GLB models via Meshy AI, upload any image → 3D |

---

## Project Structure

```
GaaZoo-Data-Ingestion/
├── backend/                    ← Single Flask app (run this)
│   ├── app.py                  ← Entry point — registers all blueprints
│   ├── config.py               ← Unified config (all env vars + paths)
│   ├── requirements.txt
│   ├── ProcessIQ_Prompts_GaaZoo.xlsx   ← AI prompt templates
│   ├── modules/                ← Shared business logic
│   │   ├── catalog_db.py       ← Neo4j graph schema + CRUD
│   │   ├── amazon_client.py    ← Amazon RapidAPI
│   │   ├── serp_client.py      ← Google SerpAPI + web scraping
│   │   ├── meshy_client.py     ← Meshy AI image→3D
│   │   ├── image_utils.py      ← Pillow helpers
│   │   ├── model_scaler.py     ← GLB real-world scaling (trimesh)
│   │   ├── gemini_catalog.py   ← Gemini 1.5 Flash (catalog analysis)
│   │   ├── dpp_builder.py      ← DPP construction from signals
│   │   ├── gemini_ai.py        ← ProcessIQ Vanilla Prompt API (Templates 15–26)
│   │   ├── image_analyser.py   ← Per-image analysis wrapper
│   │   ├── pinterest_auth.py   ← Pinterest OAuth 2.0
│   │   ├── pinterest_fetcher.py← Pinterest API v5
│   │   ├── spotify_api.py      ← Spotify Web API
│   │   └── spotify_auth.py     ← Spotify OAuth 2.0
│   ├── pipelines/              ← End-to-end ingestion pipelines
│   │   ├── pipeline_amazon.py
│   │   ├── pipeline_serp.py
│   │   └── pipeline_3d.py
│   └── routes/                 ← Flask blueprints (one per module)
│       ├── auth_routes.py      ← /auth/*  (Pinterest + Spotify OAuth)
│       ├── profile_routes.py   ← /profile/* (DPP build + analysis)
│       ├── ai_routes.py        ← /ai/* (suggestions, Q&A)
│       ├── catalog_routes.py   ← /api/catalog/*, /api/fetch-images, ...
│       └── viewer_routes.py    ← /, /dpp, /generate-3d, /proxy-glb, ...
├── frontend/
│   ├── index.html              ← Unified SPA (sidebar + 3 module panels)
│   └── dpp.html                ← Design Profile standalone page (/dpp)
├── data/
│   ├── 2d/                     ← Downloaded product images
│   └── 3d/                     ← Generated GLB models
├── .env.example                ← Copy to .env and fill in API keys
└── README.md
```

---

## Quick Start

### 1. Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Start Neo4j

Start your local Neo4j instance (or use Aura) and update `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` in `.env`.

### 4. Run the server

```bash
cd backend
python app.py
```

Open **http://localhost:5000** in your browser.

---

## API Routes

| Prefix | Module | Description |
|---|---|---|
| `/` | Viewer | Serves the unified frontend |
| `/dpp` | Viewer | Serves the DPP frontend |
| `/auth/*` | Profile | Pinterest + Spotify OAuth |
| `/profile/*` | Profile | DPP build, Pinterest boards, Spotify playlists |
| `/ai/*` | Profile | Design suggestions, narrative, Q&A |
| `/api/catalog/*` | Pipeline | Neo4j catalog CRUD |
| `/api/fetch-images` | Pipeline | Amazon / Google image ingestion |
| `/api/add-local-vendor` | Pipeline | Manual product upload |
| `/api/convert-item` | Pipeline | Single item → 3D via Meshy |
| `/api/convert-selected` | Pipeline | Batch pending items → 3D |
| `/generate-3d` | Viewer | Upload any image → 3D |
| `/scale-3d` | Viewer | Upload .glb/.obj → scale (1 dim) or resize (2–3 dims) |
| `/proxy-glb` | Viewer | Proxy Meshy GLB URLs (CORS) |
| `/api/files/*` | Viewer | Serve local images and GLB files |
| `/health` | App | Health check |

---

## Environment Variables

See [`.env.example`](.env.example) for the full list of required and optional variables.

Required for core functionality:
- `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` — Neo4j catalog
- `MESHY_API_KEY` — 3D conversion
- `RAPIDAPI_KEY` — Amazon image fetching
- `SERPAPI_KEY` — Google Images fetching
- `PINTEREST_APP_ID` / `PINTEREST_APP_SECRET` — Pinterest DPP
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — Spotify DPP
- `GEMINI_API_KEY` — AI image analysis
