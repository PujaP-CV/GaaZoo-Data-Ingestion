"""
modules/spotify_api.py
-----------------------
Spotify Web API calls for GaaZoo: user playlists, playlist tracks, audio features.
Uses access token from session (obtain via spotify_auth.get_valid_spotify_token).
"""

import logging

import requests
from modules.spotify_auth import refresh_access_token

logger = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"


def _get_token(session: dict = None) -> str | None:
    """Return valid token from the provided session dict, refreshing if needed."""
    if not session:
        return None
    token   = session.get("spotify_access_token")
    refresh = session.get("spotify_refresh_token")
    if token:
        return token
    if refresh:
        try:
            data      = refresh_access_token(refresh)
            new_token = data.get("access_token")
            if new_token:
                session["spotify_access_token"] = new_token
                if data.get("refresh_token"):
                    session["spotify_refresh_token"] = data["refresh_token"]
                return new_token
        except Exception as e:
            logger.warning(f"Spotify token refresh failed: {e}")
    return None


def _request(method: str, url: str, token: str, **kwargs) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"}
    return requests.request(method, url, headers=headers, timeout=15, **kwargs)


def fetch_user_playlists(token: str = None):
    """
    Fetch current user's playlists (all pages).
    Returns (list of { id, name, description, track_count }, status_code_or_None).
    status_code is None on success; on API error it is the HTTP status (e.g. 403).
    """
    token = token or get_valid_spotify_token()
    if not token:
        return [], None
    out = []
    url = f"{SPOTIFY_API_BASE}/me/playlists?limit=50"
    last_status = None
    while url:
        r = _request("GET", url, token)
        if r.status_code == 401:
            # Try refresh and one retry
            new_token = get_valid_spotify_token()
            if new_token and new_token != token:
                token = new_token
                r = _request("GET", url, token)
        if r.status_code != 200:
            last_status = r.status_code
            logger.warning(f"Spotify playlists API {r.status_code}: {r.text[:200]}")
            break
        data = r.json()
        for item in data.get("items", []):
            # Spotify API returns 'items' (not 'tracks') at playlist level for the new API format
            # Fall back to 'tracks' for older API responses
            items_obj = item.get("items") or item.get("tracks") or {}
            tracks_total = items_obj.get("total") if isinstance(items_obj, dict) else None
            out.append({
                "id": item.get("id"),
                "name": item.get("name") or "",
                "description": (item.get("description") or "").strip()[:200],
                "track_count": tracks_total or 0,
            })
        url = data.get("next")
    return out, last_status


def fetch_playlist_tracks(token: str, playlist_id: str, max_tracks: int = 100):
    """
    Fetch tracks for one playlist.
    Returns (list of { id, name, artist, artist_names }, None) on success.
    Returns ([], status_code) on error so caller can detect 403.
    """
    token = token or get_valid_spotify_token()
    if not token or not playlist_id:
        return [], None
    out = []
    url = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/items?limit=100"
    while url and len(out) < max_tracks:
        r = _request("GET", url, token)
        if r.status_code == 401:
            new_token = get_valid_spotify_token()
            if new_token and new_token != token:
                token = new_token
                r = _request("GET", url, token)
        if r.status_code != 200:
            logger.warning(f"Spotify playlist tracks {r.status_code}: {r.text[:200]}")
            return out, r.status_code
        data = r.json()
        for item in data.get("items", []):
            # New API uses 'item', deprecated API uses 'track' — support both
            track = item.get("item") or item.get("track")
            if not track or not track.get("id"):
                continue
            artists = track.get("artists") or []
            artist_names = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            out.append({
                "id": track.get("id"),
                "name": track.get("name") or "",
                "artist": artist_names,
                "artist_names": artist_names,
            })
            if len(out) >= max_tracks:
                break
        url = data.get("next") if len(out) < max_tracks else None
    return out, None


def fetch_audio_features(token: str, track_ids: list) -> list:
    """
    Fetch audio features for up to 100 track IDs.
    Returns list of dicts with energy, valence, tempo, acousticness, danceability, instrumentalness.
    """
    token = token or get_valid_spotify_token()
    if not token or not track_ids:
        return []
    ids = track_ids[:100]
    ids_str = ",".join(ids)
    url = f"{SPOTIFY_API_BASE}/audio-features?ids={ids_str}"
    r = _request("GET", url, token)
    if r.status_code == 401:
        new_token = get_valid_spotify_token()
        if new_token and new_token != token:
            r = _request("GET", url, new_token)
    if r.status_code != 200:
        logger.warning(f"Spotify audio-features {r.status_code}: {r.text[:200]}")
        return []
    data = r.json()
    return data.get("audio_features") or []


def build_user_summary_from_live(
    token: str,
    playlist_ids: list,
    playlist_names: list,
    max_tracks_per_playlist: int = 30,
    max_total_tracks: int = 150,
):
    """
    Fetch tracks from selected playlists via API and build (top_artists, top_tracks, top_genres, audio_features_str, top_genres_list).
    Returns the 5-tuple on success.
    Returns (None, error_message) when no tracks could be loaded (e.g. 403 Forbidden).
    """
    token = token or get_valid_spotify_token()
    if not token or not playlist_ids:
        return None, None

    all_tracks = []
    last_status = None
    tracks_per_playlist = max(max_tracks_per_playlist, max_total_tracks // max(1, len(playlist_ids)))
    for pid in playlist_ids[:20]:
        tracks, status = fetch_playlist_tracks(token, pid, max_tracks=tracks_per_playlist)
        if status is not None:
            last_status = status
        all_tracks.extend(tracks)
        if len(all_tracks) >= max_total_tracks:
            break
    all_tracks = all_tracks[:max_total_tracks]

    if not all_tracks:
        if last_status == 403:
            return None, (
                "Spotify returned Forbidden when reading playlist tracks. "
                "If your app is in Development Mode, add your Spotify account in the "
                "Spotify Developer Dashboard → your app → Settings → User Management. "
                "Then disconnect and reconnect Spotify here."
            )
        return None, "Could not load tracks from the selected playlists. Try playlists you own that have tracks."

    # Top artists (by frequency)
    artist_count = {}
    for t in all_tracks:
        a = (t.get("artist") or "").strip()
        if a:
            artist_count[a] = artist_count.get(a, 0) + 1
    top_artists_list = sorted(artist_count.keys(), key=lambda x: -artist_count[x])[:15]
    top_artists = ", ".join(top_artists_list) if top_artists_list else "unknown"

    # Top tracks (name – artist)
    top_tracks_list = [{"name": t.get("name", ""), "artist": t.get("artist", "")} for t in all_tracks[:25]]
    top_tracks = "; ".join(f"{t['name']} – {t['artist']}" for t in top_tracks_list) if top_tracks_list else "unknown"

    # Genres: Spotify Web API doesn't return genre per track; we leave empty and Template 24 can infer from artists/tracks
    top_genres_list = []
    top_genres = "inferred from artists and tracks"

    # Audio features
    track_ids = [t["id"] for t in all_tracks if t.get("id")][:100]
    features = fetch_audio_features(token, track_ids)
    if features:
        valid = [f for f in features if f and isinstance(f, dict)]
        if valid:
            n = len(valid)
            af = {
                "energy": round(sum(f.get("energy") for f in valid if f.get("energy") is not None) / n, 2),
                "valence": round(sum(f.get("valence") for f in valid if f.get("valence") is not None) / n, 2),
                "tempo": int(round(sum(f.get("tempo") or 0 for f in valid) / n)),
                "acousticness": round(sum(f.get("acousticness") for f in valid if f.get("acousticness") is not None) / n, 2),
                "danceability": round(sum(f.get("danceability") for f in valid if f.get("danceability") is not None) / n, 2),
                "instrumentalness": round(sum(f.get("instrumentalness") for f in valid if f.get("instrumentalness") is not None) / n, 2),
            }
            import json as _json
            audio_features_str = _json.dumps(af)
        else:
            audio_features_str = "not provided"
    else:
        audio_features_str = "not provided"

    return (top_artists, top_tracks, top_genres, audio_features_str, top_genres_list), None
