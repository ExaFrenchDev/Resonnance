from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from config import Config
from modules import auth, database, mailer

bp = Blueprint("auth", __name__)


def base_url():
    return request.url_root.rstrip("/")


@bp.get("/")
def landing():
    if auth.current_user():
        return redirect(url_for("match.discover_page"))
    return render_template("landing.html")


@bp.get("/inscription")
def register_page():
    if auth.current_user():
        return redirect(url_for("match.discover_page"))
    return render_template("register.html")


@bp.post("/api/auth/register")
def register_api():
    data = request.get_json(silent=True) or request.form
    try:
        user_id = auth.register(
            data.get("email"),
            data.get("username"),
            data.get("password"),
            data.get("display_name", ""),
            str(data.get("newsletter", "1")) not in ("0", "false", "False"),
            base_url(),
        )
    except auth.AuthError as error:
        return jsonify({"ok": False, "error": error.message, "field": error.field}), 400

    session["pending_user_id"] = user_id
    return jsonify({"ok": True, "redirect": url_for("auth.verify_page")})


@bp.get("/confirmation")
def verify_page():
    user_id = session.get("user_id") or session.get("pending_user_id")
    if not user_id:
        return redirect(url_for("auth.register_page"))
    user = database.query_one("SELECT id, email, is_verified FROM users WHERE id = ?", (user_id,))
    if not user:
        session.clear()
        return redirect(url_for("auth.register_page"))
    if user["is_verified"]:
        return redirect(url_for("music.genres_page"))
    masked = user["email"]
    name, _, domain = masked.partition("@")
    masked = f"{name[:2]}{'•' * max(2, len(name) - 2)}@{domain}"
    return render_template("verify.html", masked_email=masked, ttl=Config.CODE_TTL_MINUTES)


@bp.post("/api/auth/verify")
def verify_api():
    user_id = session.get("user_id") or session.get("pending_user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Session introuvable. Recommence l'inscription."}), 400
    data = request.get_json(silent=True) or request.form
    try:
        auth.confirm_code(user_id, data.get("code"))
    except auth.AuthError as error:
        return jsonify({"ok": False, "error": error.message, "field": error.field}), 400

    user = database.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    session.pop("pending_user_id", None)
    auth.open_session(user)
    return jsonify({"ok": True, "redirect": url_for("music.genres_page")})


@bp.post("/api/auth/resend")
def resend_api():
    user_id = session.get("user_id") or session.get("pending_user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Session introuvable."}), 400
    user = database.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user or user["is_verified"]:
        return jsonify({"ok": False, "error": "Ce compte est déjà confirmé."}), 400
    auth.issue_code(user["id"], user["email"], user["display_name"] or user["username"], "signup", base_url())
    return jsonify({"ok": True, "message": "Nouveau code envoyé."})


@bp.get("/connexion")
def login_page():
    if auth.current_user():
        return redirect(url_for("match.discover_page"))
    return render_template("login.html", next_url=request.args.get("next", ""))


@bp.post("/api/auth/login")
def login_api():
    data = request.get_json(silent=True) or request.form
    try:
        user = auth.login(data.get("identifier"), data.get("password"))
    except auth.AuthError as error:
        return jsonify({"ok": False, "error": error.message, "field": error.field}), 401

    auth.open_session(user)
    if not user["is_verified"]:
        return jsonify({"ok": True, "redirect": url_for("auth.verify_page")})
    if user["onboarding_step"] == "genres":
        return jsonify({"ok": True, "redirect": url_for("music.genres_page")})
    if user["onboarding_step"] == "tracks":
        return jsonify({"ok": True, "redirect": url_for("music.tracks_page")})
    target = data.get("next") or url_for("match.discover_page")
    return jsonify({"ok": True, "redirect": target})


@bp.get("/deconnexion")
def logout():
    auth.close_session()
    return redirect(url_for("auth.landing"))


@bp.get("/parametres")
@auth.login_required
def settings_page():
    user = auth.current_user()
    return render_template("settings.html", user=user, is_admin=auth.is_admin(user))


@bp.post("/api/profile")
@auth.login_required
def update_profile():
    user = auth.current_user()
    data = request.get_json(silent=True) or request.form
    display_name = (data.get("display_name") or user["username"]).strip()[:40]
    bio = (data.get("bio") or "").strip()[:280]
    city = (data.get("city") or "").strip()[:60]
    newsletter = 1 if str(data.get("newsletter", "1")) not in ("0", "false", "False") else 0
    try:
        birth_year = int(data.get("birth_year") or 0) or None
    except (TypeError, ValueError):
        birth_year = None
    if birth_year and not (1920 <= birth_year <= 2012):
        return jsonify({"ok": False, "error": "Année de naissance invalide."}), 400

    database.execute(
        """UPDATE users SET display_name = ?, bio = ?, city = ?, birth_year = ?, newsletter = ?
           WHERE id = ?""",
        (display_name, bio, city, birth_year, newsletter, user["id"]),
    )
    return jsonify({"ok": True, "message": "Profil enregistré."})


@bp.post("/api/account/password")
@auth.login_required
def change_password_api():
    user = auth.current_user()
    data = request.get_json(silent=True) or {}
    try:
        auth.change_password(user, data.get("current_password"), data.get("new_password"))
    except auth.AuthError as error:
        return jsonify({"ok": False, "error": error.message, "field": error.field}), 400
    return jsonify({"ok": True, "message": "Mot de passe modifié."})


@bp.post("/api/account/delete")
@auth.login_required
def delete_account_api():
    user = auth.current_user()
    data = request.get_json(silent=True) or {}
    try:
        auth.delete_account(user, data.get("password"), data.get("confirmation"))
    except auth.AuthError as error:
        return jsonify({"ok": False, "error": error.message, "field": error.field}), 400
    return jsonify({"ok": True, "redirect": url_for("auth.landing")})


@bp.post("/api/announcements")
@auth.login_required
def send_announcement():
    user = auth.current_user()
    if not auth.is_admin(user):
        return jsonify({"ok": False, "error": "Réservé à l'équipe."}), 403
    data = request.get_json(silent=True) or request.form
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    if not title or not body:
        return jsonify({"ok": False, "error": "Titre et message obligatoires."}), 400
    _, count = mailer.broadcast_announcement(title, body, base_url())
    return jsonify({"ok": True, "message": f"Annonce envoyée à {count} membre(s)."})
