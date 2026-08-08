import concurrent.futures
import logging
import random
import re
import threading
import time
import unicodedata

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config

log = logging.getLogger("resonance.music")
logging.basicConfig(level=logging.INFO)

ITUNES_API = "https://itunes.apple.com"
APPLE_RSS = "https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/{limit}/songs.json"
STORE_COUNTRY = "fr"
STORE_LANG = "fr_fr"
DEEZER_GENRES = "https://api.deezer.com/genre"

_cache = {}
_cache_lock = threading.Lock()
_refreshing = set()

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


def _store(key, value, ttl):
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
        if len(_cache) > _MAX_CACHE_ENTRIES:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])
            for stale_key, _ in oldest[: len(_cache) // 4]:
                _cache.pop(stale_key, None)


def _refresh_async(key, producer, ttl):
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            _store(key, producer(), ttl)
        except Exception:
            with _cache_lock:
                entry = _cache.get(key)
                if entry:
                    _cache[key] = (time.time() + 60, entry[1])
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _compute(key, producer, ttl):
    value = producer()
    _store(key, value, ttl)
    return value


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
    started = time.time()
    try:
        response = _session.get(url, params=params or {}, timeout=(3, Config.HTTP_TIMEOUT))
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        log.warning("GET %s ECHEC %.0f ms (%s)", url, (time.time() - started) * 1000, error.__class__.__name__)
        raise MusicApiError(f"Service musical injoignable ({error.__class__.__name__}).")
    except ValueError:
        raise MusicApiError("Réponse illisible du service musical.")
    log.info("GET %s -> %.0f ms", url, (time.time() - started) * 1000)
    if isinstance(payload, dict) and payload.get("errorMessage"):
        raise MusicApiError(payload["errorMessage"])
    return payload


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

_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*(remaster|remix|live|version|edit|mix|feat|avec|bonus|"
    r"deluxe|radio|extended|instrumental|acoustic|explicit)[^\)\]]*[\)\]]",
    re.IGNORECASE,
)


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _canonical(name):
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


def _dedupe_key(track):
    def strip(text):
        text = _NOISE.sub("", text or "")
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]+", "", text.lower())

    return f"{strip(track['artist_name'])}::{strip(track['title'])}"


def dedupe_key(title, artist_name):
    return _dedupe_key({"title": title or "", "artist_name": artist_name or ""})


def _clean_tracks(items, genre_id=None):
    seen_ids = set()
    seen_keys = set()
    output = []
    for raw in items:
        track = normalise_track(raw, genre_id)
        if not track["track_id"] or not track["preview"]:
            continue
        key = _dedupe_key(track)
        if track["track_id"] in seen_ids or key in seen_keys:
            continue
        seen_ids.add(track["track_id"])
        seen_keys.add(key)
        output.append(track)
    return output


def _rank(results, query):
    q = _norm(query)
    tokens = [t for t in q.split() if t]

    def score(raw):
        title = _norm(raw.get("trackName") or "")
        artist = _norm(raw.get("artistName") or "")
        hay = f"{artist} {title}"
        value = 0

        if title == q:
            value += 140
        if artist == q:
            value += 110
        if artist and q.startswith(artist + " "):
            rest = q[len(artist):].strip()
            if rest and title == rest:
                value += 200
            elif rest and rest in title:
                value += 90
        if title and q.endswith(" " + title):
            value += 70
        if tokens and all(t in hay for t in tokens):
            value += 60
        if title.startswith(q):
            value += 35
        if artist.startswith(q):
            value += 20
        value += sum(10 for t in tokens if t in hay)

        if _NOISE.search(raw.get("trackName") or ""):
            value -= 40
        if raw.get("trackExplicitness") == "cleaned":
            value -= 8
        return -value

    return sorted(results, key=score)


def _lookup(ids):
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}

    chunks = [ids[i : i + 100] for i in range(0, len(ids), 100)]

    out = {}
    for chunk in chunks:
        payload = _get(
            f"{ITUNES_API}/lookup",
            {
                "id": ",".join(chunk),
                "country": STORE_COUNTRY,
                "lang": STORE_LANG,
                "entity": "song",
            },
        )
        for raw in payload.get("results", []):
            if raw.get("wrapperType") == "track" and raw.get("trackId"):
                out[str(raw["trackId"])] = raw
    return out


def get_track(track_id):
    def producer():
        raw = _lookup([track_id]).get(str(int(track_id)))
        if not raw:
            raise MusicApiError("Morceau introuvable.")
        return normalise_track(raw)

    return _cached(f"track:{int(track_id)}", producer, ttl=86400)


def _fr_chart_pool():
    def producer():
        feed = _get(APPLE_RSS.format(country=STORE_COUNTRY, limit=100))
        entries = (feed.get("feed") or {}).get("results") or []
        details = _lookup([e.get("id") for e in entries])

        tracks = []
        seen_keys = set()
        for entry in entries:
            raw = details.get(str(entry.get("id")))
            if not raw:
                continue
            track = normalise_track(raw)
            key = _dedupe_key(track)
            if not track["preview"] or key in seen_keys:
                continue
            seen_keys.add(key)
            tracks.append(track)
        return tracks

    return _cached("fr-chart-pool", producer, ttl=21600)


def _genre_search(genre_id, limit):
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
            seen = {_dedupe_key(t) for t in tracks}
            for candidate in _genre_search(gid, limit):
                key = _dedupe_key(candidate)
                if key in seen:
                    continue
                seen.add(key)
                tracks.append(candidate)
                if len(tracks) >= limit:
                    break

        return tracks[:limit]

    return _cached(f"chart:{gid}:{limit}", producer)


def _safe_chart(genre_id, limit=50):
    try:
        return genre_chart(genre_id, limit)
    except MusicApiError:
        return []


def genre_artists(genre_id, limit=20):
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
                "limit": limit * 3,
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

    def _search(params):
        base = {
            "media": "music",
            "entity": "song",
            "country": STORE_COUNTRY,
            "lang": STORE_LANG,
        }
        base.update(params)
        return _get(f"{ITUNES_API}/search", base).get("results", [])

    def by_song():
        return _search({"term": query, "limit": limit})

    def by_artist():
        return _search({"term": query, "attribute": "artistTerm", "limit": limit})

    def by_split():
        parts = query.split()
        if len(parts) < 2:
            return []
        for cut in range(1, min(len(parts), 4)):
            artist_part = " ".join(parts[:cut])
            title_part = " ".join(parts[cut:])
            results = _search({"term": title_part, "attribute": "songTerm", "limit": 40})
            wanted = _norm(artist_part)
            hits = [r for r in results if wanted and wanted in _norm(r.get("artistName") or "")]
            if hits:
                return hits
        return []

    def producer():
        def safe(fn):
            try:
                return fn()
            except MusicApiError:
                return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(safe, fn) for fn in (by_split, by_song, by_artist)]
            split_results, song_results, artist_results = [f.result() for f in futures]

        merged = {}
        for raw in split_results + song_results + artist_results:
            tid = raw.get("trackId")
            if tid and tid not in merged:
                merged[tid] = raw

        ranked = _rank(list(merged.values()), query)
        log.info(
            "SEARCH %r -> split=%d song=%d artist=%d fusion=%d | top: %s",
            query, len(split_results), len(song_results), len(artist_results), len(merged),
            " || ".join(f"{r.get('artistName')} - {r.get('trackName')}" for r in ranked[:5]),
        )
        return _clean_tracks(ranked)[:limit]

    return _cached(f"search:{query.lower()}:{limit}", producer, ttl=1800)


def _discovery_order(genre_ids, seed):
    genres = list(dict.fromkeys(int(g) for g in (genre_ids or [0])))
    key = f"disco:{'-'.join(map(str, genres))}:{seed}"

    def producer():
        results = {g: _safe_chart(g, 50) for g in genres}

        ordered = []
        seen = set()
        for index in range(50):
            for genre_id in genres:
                tracks = results.get(genre_id) or []
                if index >= len(tracks):
                    continue
                track = tracks[index]
                dkey = _dedupe_key(track)
                if dkey in seen:
                    continue
                seen.add(dkey)
                ordered.append(track)

        random.Random(seed).shuffle(ordered)
        return ordered

    return _cached(key, producer, ttl=3600)


def discovery_page(genre_ids, exclude_ids=(), exclude_keys=(), offset=0, size=9, seed=0):
    exclude = {int(i) for i in exclude_ids}
    keys = set(exclude_keys or ())
    ordered = [
        t for t in _discovery_order(genre_ids, seed)
        if t["track_id"] not in exclude and _dedupe_key(t) not in keys
    ]
    return {
        "tracks": ordered[offset : offset + size],
        "has_more": len(ordered) > offset + size,
        "total": len(ordered),
    }


def discovery_pool(genre_ids, exclude_ids=(), size=30):
    return discovery_page(genre_ids, exclude_ids, (), 0, size, random.randint(1, 10**6))["tracks"]


def warm_cache(blocking=False):
    def work():
        try:
            _fr_chart_pool()
            for genre in list_genres()[:12]:
                _safe_chart(genre["genre_id"], 50)
        except Exception:
            pass

    if blocking:
        work()
    else:
        threading.Thread(target=work, daemon=True).start()
