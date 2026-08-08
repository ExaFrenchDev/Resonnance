import concurrent.futures
import random
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config

# --- Sources -----------------------------------------------------------------
# Apple : catalogue scopé par pays via country=fr, sans authentification.
ITUNES_API = "https://itunes.apple.com"
APPLE_RSS = "https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/{limit}/songs.json"
STORE_COUNTRY = "fr"
STORE_LANG = "fr_fr"

# Deezer : conservé UNIQUEMENT pour la liste des genres (taxonomie statique,
# pas de géolocalisation). Ça préserve les genre_id déjà stockés en base.
DEEZER_GENRES = "https://api.deezer.com/genre"

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

FALLBACK_COVER = "https://is1-ssl.mzstatic.com/image/thumb/Features/v4/placeholder/400x400bb.jpg"


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
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight[key] = threading.Event()

    def worker():
        try:
            _store(key, producer(), ttl)
        except Exception:
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


def _get(url, params=None):
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
    if isinstance(payload, dict) and payload.get("errorMessage"):
        raise MusicApiError(payload["errorMessage"])
    return payload


# ---------------------------------------------------------------------------
# Genres : mapping nom Apple -> genre_id Deezer (préserve la base existante)
# ---------------------------------------------------------------------------

_GENRE_ALIASES = {
    "rap": ("rap", "hip-hop", "hip hop"),
    "rnb": ("r&b", "rnb", "soul", "funk"),
    "metal": ("metal", "métal", "hard rock"),
    "alternative": ("alternative", "indie", "indé"),
    "electro": ("electro", "électro", "electronic", "électronique", "house", "techno"),
    "dance": ("dance", "danse"),
    "reggae": ("reggae", "dancehall", "ragga"),
    "classical": ("classique", "classical", "opéra", "opera"),
    "soundtrack": ("soundtrack", "bande", "film", "jeux", "game"),
    "latin": ("latin", "latino"),
    "world": ("world", "monde", "afric", "asiat", "brésil", "bresil", "indienne", "arab"),
    "kids": ("enfant", "children", "kids", "jeunesse"),
    "country": ("country",),
    "blues": ("blues",),
    "jazz": ("jazz",),
    "folk": ("folk", "songwriter", "chanson"),
    "rock": ("rock",),
    "pop": ("pop", "variété", "variete"),
}

# Terme de genre côté Apple, pour le repli par recherche.
_APPLE_GENRE_TERMS = {
    "rap": "Hip-Hop/Rap",
    "rnb": "R&B/Soul",
    "metal": "Metal",
    "alternative": "Alternative",
    "electro": "Electronic",
    "dance": "Dance",
    "reggae": "Reggae",
    "classical": "Classical",
    "soundtrack": "Soundtrack",
    "latin": "Latin",
    "world": "Worldwide",
    "kids": "Children's Music",
    "country": "Country",
    "blues": "Blues",
    "jazz": "Jazz",
    "folk": "Singer/Songwriter",
    "rock": "Rock",
    "pop": "Pop",
}


def _canonical(name):
    """Réduit un nom de genre (Apple ou Deezer, FR ou EN) à une clé commune."""
    text = (name or "").strip().lower()
    if not text:
        return ""
    for key, aliases in _GENRE_ALIASES.items():
        for alias in aliases:
            if alias in text:
                return key
    return ""


def list_genres():
    def producer():
        payload = _get(DEEZER_GENRES)
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


def _genre_index():
    """{clé canonique: genre_id}. Premier genre Deezer qui matche gagne."""

    def producer():
        index = {}
        for genre in list_genres():
            key = _canonical(genre["genre_name"])
            if key and key not in index:
                index[key] = genre["genre_id"]
        return index

    return _cached("genre-index", producer, ttl=86400)


def _genre_id_for(apple_genre_name):
    return _genre_index().get(_canonical(apple_genre_name), 0)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _artwork(url):
    if not url:
        return FALLBACK_COVER
    return url.replace("100x100bb", "400x400bb").replace("60x60bb", "400x400bb")


def normalise_track(raw, genre_id=None):
    if genre_id is None:
        genre_id = _genre_id_for(raw.get("primaryGenreName"))
    return {
        "track_id": int(raw.get("trackId") or 0),
        "title": raw.get("trackName") or raw.get("trackCensoredName") or "Sans titre",
        "artist_id": int(raw.get("artistId") or 0),
        "artist_name": raw.get("artistName") or "Artiste inconnu",
        "album_title": raw.get("collectionName") or "",
        "cover": _artwork(raw.get("artworkUrl100")),
        "preview": raw.get("previewUrl") or "",
        "duration": int((raw.get("trackTimeMillis") or 0) // 1000),
        "genre_id": int(genre_id or 0),
        "link": raw.get("trackViewUrl") or "",
    }


def _clean_tracks(items, genre_id=None):
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
# Morceaux
# ---------------------------------------------------------------------------


def _lookup(ids):
    """Résout jusqu'à 100 IDs par requête. Renvoie {track_id: objet brut}."""
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}

    chunks = [ids[i : i + 100] for i in range(0, len(ids), 100)]

    def fetch(chunk):
        payload = _get(
            f"{ITUNES_API}/lookup",
            {
                "id": ",".join(chunk),
                "country": STORE_COUNTRY,
                "lang": STORE_LANG,
                "entity": "song",
            },
        )
        return payload.get("results", [])

    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(chunks))) as pool:
        for results in pool.map(fetch, chunks):
            for raw in results:
                if raw.get("wrapperType") == "track" and raw.get("trackId"):
                    out[str(raw["trackId"])] = raw
    return out


def get_track(track_id):
    """Les previews Apple sont des fichiers CDN statiques : pas d'expiration."""

    def producer():
        raw = _lookup([track_id]).get(str(int(track_id)))
        if not raw:
            raise MusicApiError("Morceau introuvable.")
        return normalise_track(raw)

    return _cached(f"track:{int(track_id)}", producer, ttl=86400)


def _fr_chart_pool():
    """Top 100 français réel (classement Apple FR), previews et genres inclus."""

    def producer():
        feed = _get(APPLE_RSS.format(country=STORE_COUNTRY, limit=100))
        entries = (feed.get("feed") or {}).get("results") or []
        details = _lookup([e.get("id") for e in entries])

        tracks = []
        for entry in entries:
            raw = details.get(str(entry.get("id")))
            if not raw:
                continue
            track = normalise_track(raw)
            if not track["preview"]:
                continue
            tracks.append(track)
        return tracks

    return _cached("fr-chart-pool", producer, ttl=21600)


def _genre_search(genre_id, limit):
    """Repli : recherche par genre dans le store français."""
    term = _APPLE_GENRE_TERMS.get(_canonical(genre_name_map().get(genre_id, "")))
    if not term:
        return []
    payload = _get(
        f"{ITUNES_API}/search",
        {
            "term": term,
            "attribute": "genreTerm",
            "media": "music",
            "entity": "song",
            "country": STORE_COUNTRY,
            "lang": STORE_LANG,
            "limit": min(limit * 2, 200),
        },
    )
    return _clean_tracks(payload.get("results", []), genre_id)


def genre_chart(genre_id, limit=40):
    gid = int(genre_id or 0)

    def producer():
        pool = _fr_chart_pool()
        tracks = list(pool) if gid == 0 else [t for t in pool if t["genre_id"] == gid]

        if len(tracks) < 8 and gid:
            seen = {t["track_id"] for t in tracks}
            for candidate in _genre_search(gid, limit):
                if candidate["track_id"] in seen:
                    continue
                seen.add(candidate["track_id"])
                tracks.append(candidate)
                if len(tracks) >= limit:
                    break

        return tracks[:limit]

    return _cached(f"chart:{gid}:{limit}", producer)


def genre_artists(genre_id, limit=20):
    """Artistes dérivés du contenu français du genre, pas d'un top mondial."""

    def producer():
        seen = {}
        for track in genre_chart(int(genre_id), limit=50):
            aid = track["artist_id"]
            if aid and aid not in seen:
                seen[aid] = {
                    "artist_id": aid,
                    "artist_name": track["artist_name"],
                    "picture": track["cover"],
                }
            if len(seen) >= limit:
                break
        return list(seen.values())

    return _cached(f"genre-artists:{genre_id}:{limit}", producer, ttl=21600)


def artist_top_tracks(artist_id, limit=10):
    def producer():
        payload = _get(
            f"{ITUNES_API}/lookup",
            {
                "id": int(artist_id),
                "entity": "song",
                "limit": limit + 1,
                "country": STORE_COUNTRY,
                "lang": STORE_LANG,
            },
        )
        results = [r for r in payload.get("results", []) if r.get("wrapperType") == "track"]
        return _clean_tracks(results)[:limit]

    return _cached(f"artist-top:{artist_id}:{limit}", producer, ttl=86400)


def search_tracks(query, limit=25):
    query = (query or "").strip()
    if len(query) < 2:
        return []

    def producer():
        payload = _get(
            f"{ITUNES_API}/search",
            {
                "term": query,
                "media": "music",
                "entity": "song",
                "country": STORE_COUNTRY,
                "lang": STORE_LANG,
                "limit": limit,
            },
        )
        return _clean_tracks(payload.get("results", []))

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
    def worker():
        try:
            genres = list_genres()
            _fr_chart_pool()
            for genre in genres[:12]:
                try:
                    genre_chart(genre["genre_id"], limit=50)
                except MusicApiError:
                    continue
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()
