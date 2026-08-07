import os
from dotenv import load_dotenv

load_dotenv()


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "resonance-dev-key-a-changer")
    DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "resonance.db"))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30

    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = _int("SMTP_PORT", 587)
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").replace(" ", "")
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1") == "1"
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "Resonance")
    MAIL_FROM_ADDRESS = os.getenv("MAIL_FROM_ADDRESS", os.getenv("SMTP_USER", "no-reply@resonance.app"))

    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    RESEND_FROM_ADDRESS = os.getenv("RESEND_FROM_ADDRESS", "onboarding@resend.dev")

    DEEZER_API = "https://api.deezer.com"
    HTTP_TIMEOUT = _int("HTTP_TIMEOUT", 10)
    CACHE_TTL = _int("CACHE_TTL", 3600)

    MIN_GENRES = _int("MIN_GENRES", 3)
    MIN_TRACKS = _int("MIN_TRACKS", 8)
    STRONG_MATCH = _int("STRONG_MATCH", 70)
    MATCH_ALERT_THRESHOLD = _int("MATCH_ALERT_THRESHOLD", 80)

    WEIGHT_GENRES = 0.45
    WEIGHT_ARTISTS = 0.35
    WEIGHT_TRACKS = 0.20

    CODE_TTL_MINUTES = _int("CODE_TTL_MINUTES", 20)
    ADMIN_EMAILS = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]

    ICE_SERVERS = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ]
