import re
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from modules import database, mailer

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.]{3,20}$")


class AuthError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field


def _now():
    return datetime.utcnow()


def validate_email(value):
    value = (value or "").strip().lower()
    if not EMAIL_PATTERN.match(value):
        raise AuthError("Cette adresse email n'est pas valide.", "email")
    return value


def validate_username(value):
    value = (value or "").strip()
    if not USERNAME_PATTERN.match(value):
        raise AuthError("3 à 20 caractères, lettres, chiffres, point ou tiret bas.", "username")
    return value


def validate_password(value):
    value = value or ""
    if len(value) < 8:
        raise AuthError("Le mot de passe doit faire au moins 8 caractères.", "password")
    if value.isdigit() or value.isalpha():
        raise AuthError("Mélange lettres et chiffres pour un mot de passe solide.", "password")
    return value


def generate_code():
    return f"{secrets.randbelow(1000000):06d}"


def issue_code(user_id, email, display_name, purpose="signup", base_url=""):
    code = generate_code()
    expires = (_now() + timedelta(minutes=Config.CODE_TTL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    database.execute("UPDATE verification_codes SET used = 1 WHERE user_id = ? AND purpose = ?", (user_id, purpose))
    database.execute(
        "INSERT INTO verification_codes (user_id, code, purpose, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, code, purpose, expires),
    )
    mailer.send_verification_code(email, display_name, code, base_url)
    return code


def register(email, username, password, display_name="", newsletter=True, base_url=""):
    email = validate_email(email)
    username = validate_username(username)
    validate_password(password)

    if database.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        raise AuthError("Un compte existe déjà avec cette adresse.", "email")
    if database.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise AuthError("Ce pseudo est déjà pris.", "username")

    user_id = database.execute(
        """INSERT INTO users (email, username, password_hash, display_name, avatar_seed, newsletter)
           VALUES (?, ?, ?, ?, ?, ?) RETURNING id""",
        (
            email,
            username,
            generate_password_hash(password),
            (display_name or username).strip(),
            secrets.token_hex(6),
            1 if newsletter else 0,
        ),
    )
    issue_code(user_id, email, display_name or username, "signup", base_url)
    return user_id


def confirm_code(user_id, code, purpose="signup"):
    record = database.query_one(
        """SELECT * FROM verification_codes
           WHERE user_id = ? AND purpose = ? AND used = 0
           ORDER BY id DESC LIMIT 1""",
        (user_id, purpose),
    )
    if not record:
        raise AuthError("Aucun code en attente. Demande un nouvel envoi.", "code")
    if datetime.strptime(record["expires_at"], "%Y-%m-%d %H:%M:%S") < _now():
        raise AuthError("Ce code a expiré. Demande un nouvel envoi.", "code")
    if record["code"] != (code or "").strip():
        raise AuthError("Code incorrect.", "code")

    database.execute("UPDATE verification_codes SET used = 1 WHERE id = ?", (record["id"],))
    database.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
    return True


def login(identifier, password):
    identifier = (identifier or "").strip().lower()
    user = database.query_one(
        "SELECT * FROM users WHERE lower(email) = ? OR lower(username) = ?",
        (identifier, identifier),
    )
    if not user or not check_password_hash(user["password_hash"], password or ""):
        raise AuthError("Identifiants incorrects.", "password")
    return user


def open_session(user):
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    database.execute("UPDATE users SET last_seen = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = ?", (user["id"],))


def close_session():
    session.clear()


def current_user():
    if "user" in g:
        return g.user
    user_id = session.get("user_id")
    g.user = database.query_one("SELECT * FROM users WHERE id = ?", (user_id,)) if user_id else None
    return g.user


def _unauthorised():
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Session expirée. Reconnecte-toi."}), 401
    return redirect(url_for("auth.login_page", next=request.path))


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return _unauthorised()
        if not user["is_verified"]:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Confirme ton adresse email."}), 403
            return redirect(url_for("auth.verify_page"))
        return view(*args, **kwargs)

    return wrapper


def onboarding_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        user = current_user()
        if user["onboarding_step"] != "done":
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Termine ton profil musical."}), 403
            target = "music.genres_page" if user["onboarding_step"] == "genres" else "music.tracks_page"
            return redirect(url_for(target))
        return view(*args, **kwargs)

    return wrapper


def change_password(user, current_password, new_password):
    if not check_password_hash(user["password_hash"], current_password or ""):
        raise AuthError("Mot de passe actuel incorrect.", "current_password")
    validate_password(new_password)
    if check_password_hash(user["password_hash"], new_password):
        raise AuthError("Choisis un mot de passe différent de l'actuel.", "new_password")
    database.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), user["id"]),
    )
    return True


def delete_account(user, password, confirmation):
    if not check_password_hash(user["password_hash"], password or ""):
        raise AuthError("Mot de passe incorrect.", "password")
    if (confirmation or "").strip().upper() != "SUPPRIMER":
        raise AuthError("Écris SUPPRIMER en majuscules pour confirmer.", "confirmation")

    database.execute(
        """DELETE FROM call_logs WHERE conversation_id IN
           (SELECT id FROM conversations WHERE user_a = ? OR user_b = ?)""",
        (user["id"], user["id"]),
    )
    database.execute("DELETE FROM passes WHERE from_id = ? OR to_id = ?", (user["id"], user["id"]))
    database.execute("DELETE FROM profile_likes WHERE from_id = ? OR to_id = ?", (user["id"], user["id"]))
    database.execute("DELETE FROM conversations WHERE user_a = ? OR user_b = ?", (user["id"], user["id"]))
    database.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    close_session()
    return True


def is_admin(user):
    return bool(user) and user["email"].lower() in Config.ADMIN_EMAILS
