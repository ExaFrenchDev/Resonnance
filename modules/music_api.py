import random
import threading
import time

import requests

from config import Config

_cache = {}
_cache_lock = threading.Lock()
_session = requests.Session()
_session.headers.update({"User-Agent": "Resonance/1.0"})

FALLBACK_COVER = "https://e-cdns-images.dzcdn.net/images/cover/none/264x264-000000-80-0-0.jpg"


class MusicApiError(Exception):
    pass


def _cached(key, producer, ttl=None):
    ttl = ttl or Config.CACHE_TTL
    now = time.time()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > now:
            return entry[1]
    value = producer()
    with _cache_lock:
        _cache[key] = (now + ttl, value)
    return value


def _get(path, params=None):
    url = f"{Config.DEEZER_API}{path}"
    try:
        response = _session.get(url, params=params or {}, timeout=Config.HTTP_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise MusicApiError(f"Service musical injoignable ({error.__class__.__name__}).")
    except ValueError:
        raise MusicApiError("Réponse illisible du service musical.")
    if isinstance(payload, dict) and "error" in payload and payload["error"]:
        raise MusicApiError(payload["error"].get("message", "Erreur du service musical."))
    return payload


def normalise_track(raw, genre_id=0):
    artist = raw.get("artist") or {}
    album = raw.get("album") or {}
    return {
        "track_id": int(raw.get("id", 0)),
        "title": raw.get("title_short") or raw.get("title") or "Sans titre",
        "artist_id": int(artist.get("id", 0)),
        "artist_name": artist.get("name", "Artiste inconnu"),
        "album_title": album.get("title", ""),
        "cover": album.get("cover_medium") or album.get("cover") or FALLBACK_COVER,
        "preview": raw.get("preview", ""),
        "duration": int(raw.get("duration", 0)),
        "genre_id": int(genre_id or 0),
        "link": raw.get("link", ""),
    }


def _clean_tracks(items, genre_id=0):
    seen = set()
    output = []
    for raw in items:
        track = normalise_track(raw, genre_id)
        if not track["track_id"] or not track["preview"] or track["track_id"] in seen:
            continue
        seen.add(track["track_id"])
        output.append(track)
    return output


def list_genres():
    def producer():
        payload = _get("/genre")
        genres = []
        for item in payload.get("data", []):
            if int(item.get("id", 0)) == 0:
                continue
            genres.append(
                {
                    "genre_id": int(item["id"]),
                    "genre_name": item.get("name", ""),
                    "picture": item.get("picture_medium") or item.get("picture", ""),
                }
            )
        return genres

    return _cached("genres", producer, ttl=86400)


def genre_name_map():
    return {genre["genre_id"]: genre["genre_name"] for genre in list_genres()}


def get_track(track_id):
    """Récupère un morceau en direct chez Deezer.

    Les URLs d'aperçu Deezer contiennent un jeton qui expire assez vite :
    les stocker longtemps (cache de genre_chart, base de données) mène à
    des liens morts. On garde donc un cache très court ici, juste pour
    absorber les double-clics, et on relit ce endpoint à chaque lecture
    plutôt que de réutiliser une URL potentiellement périmée.
    """

    def producer():
        payload = _get(f"/track/{int(track_id)}")
        return normalise_track(payload)

    return _cached(f"track:{int(track_id)}", producer, ttl=60)


def genre_chart(genre_id, limit=40):
    def producer():
        payload = _get(f"/chart/{int(genre_id)}/tracks", {"limit": limit})
        return _clean_tracks(payload.get("data", []), genre_id)

    return _cached(f"chart:{genre_id}:{limit}", producer)


def genre_artists(genre_id, limit=20):
    def producer():
        payload = _get(f"/genre/{int(genre_id)}/artists", {"limit": limit})
        return [
            {"artist_id": int(a["id"]), "artist_name": a.get("name", ""), "picture": a.get("picture_medium", "")}
            for a in payload.get("data", [])
            if a.get("id")
        ]

    return _cached(f"genre-artists:{genre_id}:{limit}", producer, ttl=86400)


def artist_top_tracks(artist_id, limit=10):
    def producer():
        payload = _get(f"/artist/{int(artist_id)}/top", {"limit": limit})
        return _clean_tracks(payload.get("data", []))

    return _cached(f"artist-top:{artist_id}:{limit}", producer)


def search_tracks(query, limit=25):
    query = (query or "").strip()
    if len(query) < 2:
        return []

    def producer():
        payload = _get("/search/track", {"q": query, "limit": limit, "order": "RANKING"})
        return _clean_tracks(payload.get("data", []))

    return _cached(f"search:{query.lower()}:{limit}", producer, ttl=1800)


def discovery_pool(genre_ids, exclude_ids=(), size=30):
    exclude = set(int(i) for i in exclude_ids)
    pool = []
    seen = set()
    genres = list(genre_ids) or [0]
    for genre_id in genres:
        try:
            tracks = genre_chart(genre_id, limit=50)
        except MusicApiError:
            continue
        for track in tracks:
            if track["track_id"] in seen or track["track_id"] in exclude:
                continue
            seen.add(track["track_id"])
            pool.append(track)
    random.shuffle(pool)
    return pool[:size]
