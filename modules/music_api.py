import concurrent.futures
import random
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config

_cache = {}
_cache_lock = threading.Lock()
_inflight = {}
_inflight_lock = threading.Lock()

_MAX_CACHE_ENTRIES = 2000

_session = requests.Session()
_session.headers.update({"User-Agent": "Resonance/1.0"})
_adapter = HTTPAdapter(
    pool_connections=8,
    pool_maxsize=32,
    max_retries=Retry(
        total=2,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    ),
)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

FALLBACK_COVER = "https://e-cdns-images.dzcdn.net/images/cover/none/264x264-000000-80-0-0.jpg"

# Playlist officielle "Top France" de Deezer Charts.
# Un endpoint /playlist n'est pas géolocalisé : le contenu est identique
# quelle que soit l'IP du serveur, contrairement à /chart/{genre}/tracks.
FR_CHART_PLAYLIST_ID = 1109890291


class MusicApiError(Exception):
    pass


# ---------------------------------------------------------------------------
# Cache : stale-while-revalidate + single-flight
# ---------------------------------------------------------------------------


def _store(key, value, ttl):
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
        if len(_cache) > _MAX_CACHE_ENTRIES:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])
            for stale_key, _ in oldest[: len(_cache) // 4]:
                _cache.pop(stale_key, None)


def _compute(key, producer, ttl):
    """Un seul appel réseau par clé, même si N requêtes arrivent en même temps."""
    with _inflight_lock:
        event = _inflight.get(key)
        leader = event is None
        if leader:
            event = threading.Event()
            _inflight[key] = event

    if not leader:
        event.wait(timeout=Config.HTTP_TIMEOUT * 3)
        with _cache_lock:
            entry = _cache.get(key)
        if entry:
            return entry[1]
        # Le leader a échoué : on tente notre chance sans toucher au registre.
        return producer()

    try:
        value = producer()
        _store(key, value, ttl)
        return value
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        event.set()


def _refresh_async(key, producer, ttl):
    """Rafraîchit une entrée périmée en tâche de fond, sans bloquer la requête."""
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight[key] = threading.Event()

    def worker():
        try:
            _store(key, producer(), ttl)
        except Exception:
            # Deezer est down : on prolonge la version périmée de 60 s
            # au lieu de retenter à chaque requête.
            with _cache_lock:
                entry = _cache.get(key)
                if entry:
                    _cache[key] = (time.time() + 60, entry[1])
        finally:
            with _inflight_lock:
                event = _inflight.pop(key, None)
            if event:
                event.set()

    threading.Thread(target=worker, daemon=True).start()


def _cached(key, producer, ttl=None, stale_ttl=None):
    ttl = ttl or Config.CACHE_TTL
    stale_ttl = ttl * 4 if stale_ttl is None else stale_ttl
    now = time.time()

    with _cache_lock:
        entry = _cache.get(key)

    if entry:
        expires, value = entry
        if expires > now:
            return value
        if stale_ttl and expires + stale_ttl > now:
            _refresh_async(key, producer, ttl)
            return value

    return _compute(key, producer, ttl)


def _get(path, params=None):
    url = f"{Config.DEEZER_API}{path}"
    try:
        response = _session.get(
            url,
            params=params or {},
            timeout=(3, Config.HTTP_TIMEOUT),
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise MusicApiError(f"Service musical injoignable ({error.__class__.__name__}).")
    except ValueError:
        raise MusicApiError("Réponse illisible du service musical.")
    if isinstance(payload, dict) and "error" in payload and payload["error"]:
        raise MusicApiError(payload["error"].get("message", "Erreur du service musical."))
    return payload


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Genres
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Morceaux
# ---------------------------------------------------------------------------


def get_track(track_id):
    """Récupère un morceau en direct chez Deezer.

    Les URLs d'aperçu Deezer contiennent un jeton qui expire assez vite :
    les stocker longtemps (cache de genre_chart, base de données) mène à
    des liens morts. On garde donc un cache très court ici, sans fenêtre
    stale (stale_ttl=0), et on relit ce endpoint à chaque lecture plutôt
    que de réutiliser une URL potentiellement périmée.
    """

    def producer():
        payload = _get(f"/track/{int(track_id)}")
        return normalise_track(payload)

    return _cached(f"track:{int(track_id)}", producer, ttl=60, stale_ttl=0)


def _album_genre_ids(album_id):
    """Genres d'un album. Immuable côté Deezer, donc cache long."""

    def producer():
        payload = _get(f"/album/{int(album_id)}")
        data = (payload.get("genres") or {}).get("data") or []
        return [int(g["id"]) for g in data if g.get("id") is not None]

    return _cached(f"album-genres:{int(album_id)}", producer, ttl=604800)


def _fr_chart_pool():
    """Le vrai Top France, non géolocalisé, chaque titre annoté de ses genres."""

    def producer():
        payload = _get(f"/playlist/{FR_CHART_PLAYLIST_ID}/tracks", {"limit": 100})
        tracks = [t for t in payload.get("data", []) if t.get("preview")]

        album_ids = {(t.get("album") or {}).get("id") for t in tracks}
        album_ids.discard(None)

        genres_by_album = {}
        if album_ids:
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                futures = {pool.submit(_album_genre_ids, aid): aid for aid in album_ids}
                for future in concurrent.futures.as_completed(futures):
                    aid = futures[future]
                    try:
                        genres_by_album[aid] = future.result() or []
                    except MusicApiError:
                        genres_by_album[aid] = []

        for track in tracks:
            aid = (track.get("album") or {}).get("id")
            track["_genre_ids"] = genres_by_album.get(aid, [])
        return tracks

    return _cached("fr-chart-pool", producer, ttl=21600)


def genre_chart(genre_id, limit=40):
    gid = int(genre_id or 0)

    def producer():
        pool = _fr_chart_pool()
        raw = pool if gid == 0 else [t for t in pool if gid in t.get("_genre_ids", [])]
        tracks = _clean_tracks(raw, gid)

        # Genres peu représentés dans le top FR (metal, jazz, classique...) :
        # on complète avec les hits des artistes majeurs du genre.
        if len(tracks) < 8 and gid:
            seen = {t["track_id"] for t in tracks}
            for artist in genre_artists(gid, limit=8):
                for candidate in artist_top_tracks(artist["artist_id"], limit=4):
                    if candidate["track_id"] in seen:
                        continue
                    seen.add(candidate["track_id"])
                    tracks.append(dict(candidate, genre_id=gid))
                if len(tracks) >= limit:
                    break

        return tracks[:limit]

    return _cached(f"chart:{gid}:{limit}", producer)


def genre_artists(genre_id, limit=20):
    def producer():
        payload = _get(f"/genre/{int(genre_id)}/artists", {"limit": limit})
        return [
            {
                "artist_id": int(a["id"]),
                "artist_name": a.get("name", ""),
                "picture": a.get("picture_medium", ""),
            }
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


# ---------------------------------------------------------------------------
# Découverte
# ---------------------------------------------------------------------------


def discovery_pool(genre_ids, exclude_ids=(), size=30):
    exclude = {int(i) for i in exclude_ids}
    genres = list(dict.fromkeys(int(g) for g in (genre_ids or [0])))

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(genres))) as pool:
        futures = {pool.submit(genre_chart, g, 50): g for g in genres}
        for future in concurrent.futures.as_completed(futures):
            try:
                results[futures[future]] = future.result()
            except MusicApiError:
                results[futures[future]] = []

    # Round-robin : chaque genre contribue à tour de rôle, pour éviter
    # qu'un genre riche écrase tous les autres dans le feed.
    pool_tracks = []
    seen = set()
    for index in range(50):
        for genre_id in genres:
            tracks = results.get(genre_id) or []
            if index >= len(tracks):
                continue
            track = tracks[index]
            if track["track_id"] in seen or track["track_id"] in exclude:
                continue
            seen.add(track["track_id"])
            pool_tracks.append(track)
        if len(pool_tracks) >= size * 2:
            break

    random.shuffle(pool_tracks)
    return pool_tracks[:size]


# ---------------------------------------------------------------------------
# Préchauffage
# ---------------------------------------------------------------------------


def warm_cache():
    """Précharge le pool FR et les genres principaux en tâche de fond.

    À appeler depuis create_app() : le premier visiteur ne paie jamais
    les ~100 requêtes album du cold start.
    """

    def worker():
        try:
            genres = list_genres()
            _fr_chart_pool()
            for genre in genres[:12]:
                try:
                    # limit=50 doit correspondre à ce que discovery_pool demande,
                    # sinon la clé de cache diffère et le préchauffage est inutile.
                    genre_chart(genre["genre_id"], limit=50)
                except MusicApiError:
                    continue
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()
