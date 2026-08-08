import random

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from config import Config
from modules import auth, database, matching, music_api

bp = Blueprint("music", __name__)


@bp.get("/gouts/genres")
@auth.login_required
def genres_page():
    user = auth.current_user()
    try:
        genres = music_api.list_genres()
        error = None
    except music_api.MusicApiError as failure:
        genres, error = [], str(failure)
    selected = [row["genre_id"] for row in matching.genres_of(user["id"])]
    return render_template(
        "onboarding_genres.html",
        genres=genres,
        selected=selected,
        min_genres=Config.MIN_GENRES,
        api_error=error,
    )


@bp.post("/api/genres")
@auth.login_required
def save_genres():
    user = auth.current_user()
    data = request.get_json(silent=True) or {}
    raw = data.get("genres") or []
    names = music_api.genre_name_map()

    cleaned = []
    for item in raw:
        try:
            genre_id = int(item)
        except (TypeError, ValueError):
            continue
        if genre_id in names:
            cleaned.append((genre_id, names[genre_id]))

    if len(cleaned) < Config.MIN_GENRES:
        return jsonify({"ok": False, "error": f"Choisis au moins {Config.MIN_GENRES} genres."}), 400

    database.execute("DELETE FROM user_genres WHERE user_id = ?", (user["id"],))
    database.execute_many(
        """INSERT INTO user_genres (user_id, genre_id, genre_name, weight) VALUES (?, ?, ?, 1.0)
           ON CONFLICT (user_id, genre_id) DO UPDATE SET genre_name = EXCLUDED.genre_name, weight = EXCLUDED.weight""",
        [(user["id"], genre_id, name) for genre_id, name in cleaned],
    )
    session.pop("feed_seed", None)
    if user["onboarding_step"] == "genres":
        database.execute("UPDATE users SET onboarding_step = 'tracks' WHERE id = ?", (user["id"],))
    return jsonify({"ok": True, "redirect": url_for("music.tracks_page")})


@bp.get("/gouts/morceaux")
@auth.login_required
def tracks_page():
    user = auth.current_user()
    genres = matching.genres_of(user["id"])
    if len(genres) < Config.MIN_GENRES:
        return redirect(url_for("music.genres_page"))
    saved = database.query_all(
        "SELECT track_id, title, artist_name, cover, preview FROM user_tracks WHERE user_id = ? ORDER BY added_at DESC",
        (user["id"],),
    )
    return render_template(
        "onboarding_tracks.html",
        genres=genres,
        saved=saved,
        min_tracks=Config.MIN_TRACKS,
        done=user["onboarding_step"] == "done",
    )


@bp.get("/api/tracks/feed")
@auth.login_required
def tracks_feed():
    user = auth.current_user()
    genre_param = request.args.get("genre_id")
    genres = [row["genre_id"] for row in matching.genres_of(user["id"])]
    if genre_param and genre_param != "all":
        try:
            genres = [int(genre_param)]
        except ValueError:
            pass

    try:
        offset = max(0, int(request.args.get("offset", 0)))
        size = min(24, max(1, int(request.args.get("size", 9))))
    except (TypeError, ValueError):
        offset, size = 0, 9

    if request.args.get("shuffle") == "1":
        session["feed_seed"] = random.randint(1, 10**6)

    seed = session.get("feed_seed")
    if not seed:
        seed = random.randint(1, 10**6)
        session["feed_seed"] = seed

    owned = database.query_all(
        "SELECT track_id, title, artist_name FROM user_tracks WHERE user_id = ?",
        (user["id"],),
    )
    owned_ids = [row["track_id"] for row in owned]
    owned_keys = {music_api.dedupe_key(row["title"], row["artist_name"]) for row in owned}

    try:
        page = music_api.discovery_page(genres, owned_ids, owned_keys, offset, size, seed)
    except music_api.MusicApiError as failure:
        return jsonify({"ok": False, "error": str(failure)}), 502
    return jsonify({"ok": True, **page})


@bp.get("/api/preview/<int:track_id>")
@auth.login_required
def track_preview(track_id):
    try:
        track = music_api.get_track(track_id)
    except music_api.MusicApiError as failure:
        return jsonify({"ok": False, "error": str(failure)}), 502
    preview = (track or {}).get("preview") or ""
    if not preview:
        return jsonify({"ok": False, "error": "Aucun extrait disponible pour ce morceau."}), 404
    response = redirect(preview, code=302)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@bp.get("/api/tracks/search")
@auth.login_required
def tracks_search():
    query = request.args.get("q", "")
    try:
        results = music_api.search_tracks(query, limit=24)
    except music_api.MusicApiError as failure:
        return jsonify({"ok": False, "error": str(failure)}), 502
    return jsonify({"ok": True, "tracks": results})


@bp.post("/api/tracks")
@auth.login_required
def add_track():
    user = auth.current_user()
    data = request.get_json(silent=True) or {}
    try:
        track_id = int(data.get("track_id"))
        artist_id = int(data.get("artist_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Morceau invalide."}), 400
    if not track_id:
        return jsonify({"ok": False, "error": "Morceau invalide."}), 400

    database.execute(
        """INSERT INTO user_tracks
           (user_id, track_id, title, artist_id, artist_name, album_title, cover, preview, genre_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (user_id, track_id) DO UPDATE SET
               title = EXCLUDED.title,
               artist_id = EXCLUDED.artist_id,
               artist_name = EXCLUDED.artist_name,
               album_title = EXCLUDED.album_title,
               cover = EXCLUDED.cover,
               preview = EXCLUDED.preview,
               genre_id = EXCLUDED.genre_id""",
        (
            user["id"],
            track_id,
            (data.get("title") or "Sans titre")[:160],
            artist_id,
            (data.get("artist_name") or "Artiste inconnu")[:120],
            (data.get("album_title") or "")[:160],
            data.get("cover") or "",
            data.get("preview") or "",
            int(data.get("genre_id") or 0),
        ),
    )
    total = database.scalar("SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (user["id"],))
    return jsonify({"ok": True, "total": total, "ready": total >= Config.MIN_TRACKS})


@bp.delete("/api/tracks/<int:track_id>")
@auth.login_required
def remove_track(track_id):
    user = auth.current_user()
    database.execute("DELETE FROM user_tracks WHERE user_id = ? AND track_id = ?", (user["id"], track_id))
    total = database.scalar("SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (user["id"],))
    return jsonify({"ok": True, "total": total, "ready": total >= Config.MIN_TRACKS})


@bp.post("/api/onboarding/finish")
@auth.login_required
def finish_onboarding():
    user = auth.current_user()
    total = database.scalar("SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (user["id"],))
    if total < Config.MIN_TRACKS:
        return jsonify({"ok": False, "error": f"Ajoute au moins {Config.MIN_TRACKS} morceaux."}), 400
    database.execute("UPDATE users SET onboarding_step = 'done' WHERE id = ?", (user["id"],))
    return jsonify({"ok": True, "redirect": url_for("match.discover_page")})
