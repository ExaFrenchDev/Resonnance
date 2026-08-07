import math
from collections import defaultdict

from config import Config
from modules import database, music_api


_GENRE_NAMES = {}


def genre_label(genre_id):
    if genre_id in _GENRE_NAMES:
        return _GENRE_NAMES[genre_id]
    try:
        _GENRE_NAMES.update(music_api.genre_name_map())
    except music_api.MusicApiError:
        pass
    return _GENRE_NAMES.get(genre_id, "Autre")


def _spectrum(genres_a, genres_b, limit=9):
    keys = set(genres_a) | set(genres_b)
    if not keys:
        return []
    peak_a = max(genres_a.values()) if genres_a else 1.0
    peak_b = max(genres_b.values()) if genres_b else 1.0
    bands = []
    for genre_id in keys:
        left = genres_a.get(genre_id, 0.0) / peak_a if peak_a else 0.0
        right = genres_b.get(genre_id, 0.0) / peak_b if peak_b else 0.0
        bands.append(
            {
                "genre_id": genre_id,
                "label": genre_label(genre_id),
                "mine": round(min(1.0, left), 3),
                "theirs": round(min(1.0, right), 3),
                "shared": round(min(left, right), 3),
            }
        )
    bands.sort(key=lambda band: (band["shared"], band["mine"] + band["theirs"]), reverse=True)
    return bands[:limit]


def _cosine(vector_a, vector_b):
    if not vector_a or not vector_b:
        return 0.0
    shared = set(vector_a) & set(vector_b)
    if not shared:
        return 0.0
    numerator = sum(vector_a[key] * vector_b[key] for key in shared)
    norm_a = math.sqrt(sum(value * value for value in vector_a.values()))
    norm_b = math.sqrt(sum(value * value for value in vector_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return numerator / (norm_a * norm_b)


def _weighted_overlap(set_a, set_b, idf):
    if not set_a or not set_b:
        return 0.0
    shared = set_a & set_b
    if not shared:
        return 0.0
    numerator = sum(idf.get(key, 1.0) for key in shared)
    denominator = sum(idf.get(key, 1.0) for key in set_a | set_b)
    if denominator == 0:
        return 0.0
    return numerator / denominator


def load_profiles(user_ids=None):
    clause = ""
    params = []
    if user_ids:
        placeholders = ",".join("?" for _ in user_ids)
        clause = f" WHERE user_id IN ({placeholders})"
        params = list(user_ids)

    profiles = defaultdict(lambda: {"genres": defaultdict(float), "artists": {}, "tracks": set()})

    for row in database.query_all(f"SELECT user_id, genre_id, genre_name, weight FROM user_genres{clause}", params):
        profiles[row["user_id"]]["genres"][row["genre_id"]] += float(row["weight"]) * 2.0
        _GENRE_NAMES.setdefault(row["genre_id"], row["genre_name"])

    for row in database.query_all(
        f"SELECT user_id, track_id, artist_id, artist_name, genre_id FROM user_tracks{clause}", params
    ):
        profile = profiles[row["user_id"]]
        profile["tracks"].add(row["track_id"])
        profile["artists"][row["artist_id"]] = row["artist_name"]
        if row["genre_id"]:
            profile["genres"][row["genre_id"]] += 1.0

    return profiles


def artist_idf():
    total = database.scalar("SELECT COUNT(DISTINCT user_id) FROM user_tracks", default=0)
    if total < 2:
        return {}
    rows = database.query_all(
        "SELECT artist_id, COUNT(DISTINCT user_id) AS holders FROM user_tracks GROUP BY artist_id"
    )
    return {row["artist_id"]: math.log(1 + total / max(1, row["holders"])) + 0.35 for row in rows}


def compare(profile_a, profile_b, idf=None):
    idf = idf or {}
    genre_score = _cosine(dict(profile_a["genres"]), dict(profile_b["genres"]))
    artist_score = _weighted_overlap(set(profile_a["artists"]), set(profile_b["artists"]), idf)
    track_score = _weighted_overlap(profile_a["tracks"], profile_b["tracks"], {})

    raw = (
        Config.WEIGHT_GENRES * genre_score
        + Config.WEIGHT_ARTISTS * artist_score
        + Config.WEIGHT_TRACKS * track_score
    )
    curved = raw ** 0.72
    percentage = int(round(min(100.0, curved * 100)))

    shared_artist_ids = set(profile_a["artists"]) & set(profile_b["artists"])
    ranked = sorted(shared_artist_ids, key=lambda artist_id: (-idf.get(artist_id, 1.0), profile_a["artists"][artist_id]))
    shared_artists = [profile_a["artists"][artist_id] for artist_id in ranked]

    return {
        "score": percentage,
        "spectrum": _spectrum(dict(profile_a["genres"]), dict(profile_b["genres"])),
        "breakdown": {
            "genres": int(round(genre_score * 100)),
            "artistes": int(round(artist_score * 100)),
            "morceaux": int(round(track_score * 100)),
        },
        "shared_artists": shared_artists[:6],
        "shared_artist_count": len(shared_artist_ids),
        "shared_track_count": len(profile_a["tracks"] & profile_b["tracks"]),
    }


def candidates_for(user_id, limit=30, include_passed=False):
    profiles = load_profiles()
    if user_id not in profiles:
        return []
    idf = artist_idf()
    me = profiles[user_id]

    excluded = set()
    if not include_passed:
        excluded = {row["to_id"] for row in database.query_all("SELECT to_id FROM passes WHERE from_id = ?", (user_id,))}

    others = database.query_all(
        """SELECT id, username, display_name, bio, city, birth_year, avatar_seed, last_seen
           FROM users WHERE id != ? AND is_verified = 1 AND onboarding_step = 'done'""",
        (user_id,),
    )
    liked = {row["to_id"] for row in database.query_all("SELECT to_id FROM profile_likes WHERE from_id = ?", (user_id,))}
    likes_me = {row["from_id"] for row in database.query_all("SELECT from_id FROM profile_likes WHERE to_id = ?", (user_id,))}

    results = []
    for other in others:
        if other["id"] in excluded or other["id"] not in profiles:
            continue
        comparison = compare(me, profiles[other["id"]], idf)
        results.append(
            {
                "user": {
                    "id": other["id"],
                    "username": other["username"],
                    "display_name": other["display_name"] or other["username"],
                    "bio": other["bio"] or "",
                    "city": other["city"] or "",
                    "age": _age(other["birth_year"]),
                    "avatar_seed": other["avatar_seed"],
                    "last_seen": other["last_seen"],
                },
                "liked": other["id"] in liked,
                "likes_me": other["id"] in likes_me,
                "unlocked": other["id"] in liked and other["id"] in likes_me,
                **comparison,
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def score_between(user_a, user_b):
    profiles = load_profiles([user_a, user_b])
    if user_a not in profiles or user_b not in profiles:
        return 0
    return compare(profiles[user_a], profiles[user_b], artist_idf())["score"]


def top_tracks_of(user_id, limit=4):
    return database.query_all(
        """SELECT track_id, title, artist_name, cover, preview
           FROM user_tracks WHERE user_id = ? AND preview != ''
           ORDER BY added_at DESC LIMIT ?""",
        (user_id, limit),
    )


def genres_of(user_id):
    return database.query_all(
        "SELECT genre_id, genre_name FROM user_genres WHERE user_id = ? ORDER BY weight DESC", (user_id,)
    )


def _age(birth_year):
    if not birth_year:
        return None
    from datetime import date

    return max(0, date.today().year - int(birth_year))
