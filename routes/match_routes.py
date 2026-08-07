from flask import Blueprint, jsonify, render_template, request

from config import Config
from modules import auth, database, mailer, matching, messaging, realtime

bp = Blueprint("match", __name__)


@bp.get("/decouvrir")
@auth.onboarding_required
def discover_page():
    user = auth.current_user()
    return render_template("discover.html", user=user, strong=Config.STRONG_MATCH)


@bp.get("/api/matches")
@auth.onboarding_required
def matches_api():
    user = auth.current_user()
    try:
        limit = min(60, max(1, int(request.args.get("limit", 24))))
    except ValueError:
        limit = 24
    results = matching.candidates_for(user["id"], limit=limit)
    online = realtime.online_ids()
    for item in results:
        item["user"]["online"] = item["user"]["id"] in online
        item["top_tracks"] = matching.top_tracks_of(item["user"]["id"], 3)
        item["genres"] = [row["genre_name"] for row in matching.genres_of(item["user"]["id"])][:4]
    return jsonify({"ok": True, "matches": results, "strong": Config.STRONG_MATCH})


@bp.get("/profil/<username>")
@auth.onboarding_required
def profile_page(username):
    user = auth.current_user()
    other = database.query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not other:
        return render_template("not_found.html"), 404
    if other["id"] == user["id"]:
        score_data = {"score": 100, "spectrum": [], "breakdown": {"genres": 100, "artistes": 100, "morceaux": 100}, "shared_artists": [], "shared_artist_count": 0, "shared_track_count": 0}
        allowed = False
    else:
        profiles = matching.load_profiles([user["id"], other["id"]])
        if user["id"] in profiles and other["id"] in profiles:
            score_data = matching.compare(profiles[user["id"]], profiles[other["id"]], matching.artist_idf())
        else:
            score_data = {"score": 0, "spectrum": [], "breakdown": {"genres": 0, "artistes": 0, "morceaux": 0}, "shared_artists": [], "shared_artist_count": 0, "shared_track_count": 0}
        allowed, _ = messaging.can_talk(user["id"], other["id"])

    return render_template(
        "profile.html",
        user=user,
        other=other,
        match=score_data,
        allowed=allowed,
        strong=Config.STRONG_MATCH,
        tracks=matching.top_tracks_of(other["id"], 12),
        genres=matching.genres_of(other["id"]),
        online=realtime.is_online(other["id"]),
    )


@bp.post("/api/like/<int:target_id>")
@auth.onboarding_required
def like(target_id):
    user = auth.current_user()
    if target_id == user["id"]:
        return jsonify({"ok": False, "error": "Impossible de se liker soi-même."}), 400
    other = database.query_one("SELECT id, email, username, display_name FROM users WHERE id = ?", (target_id,))
    if not other:
        return jsonify({"ok": False, "error": "Profil introuvable."}), 404

    database.execute("INSERT OR IGNORE INTO profile_likes (from_id, to_id) VALUES (?, ?)", (user["id"], target_id))
    database.execute("DELETE FROM passes WHERE from_id = ? AND to_id = ?", (user["id"], target_id))

    mutual = bool(database.query_one("SELECT 1 FROM profile_likes WHERE from_id = ? AND to_id = ?", (target_id, user["id"])))
    score = matching.score_between(user["id"], target_id)
    conversation_id = messaging.open_conversation(user["id"], target_id, enforce=False) if mutual else None

    if mutual:
        realtime.push_to_user(
            target_id,
            "match:new",
            {"user_id": user["id"], "name": user["display_name"] or user["username"], "score": score, "conversation_id": conversation_id},
        )
        if score >= Config.MATCH_ALERT_THRESHOLD:
            mailer.send_match_alert(
                other["email"],
                other["display_name"] or other["username"],
                user["display_name"] or user["username"],
                score,
                request.url_root.rstrip("/"),
            )

    return jsonify({"ok": True, "mutual": mutual, "score": score, "conversation_id": conversation_id})


@bp.post("/api/pass/<int:target_id>")
@auth.onboarding_required
def pass_profile(target_id):
    user = auth.current_user()
    database.execute("INSERT OR IGNORE INTO passes (from_id, to_id) VALUES (?, ?)", (user["id"], target_id))
    database.execute("DELETE FROM profile_likes WHERE from_id = ? AND to_id = ?", (user["id"], target_id))
    return jsonify({"ok": True})


@bp.post("/api/pass/<int:target_id>/undo")
@auth.onboarding_required
def undo_pass(target_id):
    user = auth.current_user()
    database.execute("DELETE FROM passes WHERE from_id = ? AND to_id = ?", (user["id"], target_id))
    return jsonify({"ok": True})
