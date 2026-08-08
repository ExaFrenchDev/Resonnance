import threading
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

from config import Config

_lock = threading.Lock()
_initialised = False

NOW_SQL = "to_char(now(), 'YYYY-MM-DD HH24:MI:SS')"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    bio TEXT DEFAULT '',
    city TEXT DEFAULT '',
    birth_year INTEGER,
    avatar_seed TEXT,
    is_verified INTEGER NOT NULL DEFAULT 0,
    newsletter INTEGER NOT NULL DEFAULT 1,
    onboarding_step TEXT NOT NULL DEFAULT 'genres',
    created_at TEXT NOT NULL DEFAULT {NOW_SQL},
    last_seen TEXT NOT NULL DEFAULT {NOW_SQL}
);

CREATE TABLE IF NOT EXISTS verification_codes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'signup',
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT {NOW_SQL},
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_genres (
    user_id INTEGER NOT NULL,
    genre_id INTEGER NOT NULL,
    genre_name TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (user_id, genre_id),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_tracks (
    user_id INTEGER NOT NULL,
    track_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    artist_id BIGINT NOT NULL,
    artist_name TEXT NOT NULL,
    album_title TEXT DEFAULT '',
    cover TEXT DEFAULT '',
    preview TEXT DEFAULT '',
    genre_id INTEGER DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT {NOW_SQL},
    PRIMARY KEY (user_id, track_id),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS profile_likes (
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT {NOW_SQL},
    PRIMARY KEY (from_id, to_id),
    FOREIGN KEY (from_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (to_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS passes (
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT {NOW_SQL},
    PRIMARY KEY (from_id, to_id),
    FOREIGN KEY (from_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    user_a INTEGER NOT NULL,
    user_b INTEGER NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT {NOW_SQL},
    updated_at TEXT NOT NULL DEFAULT {NOW_SQL},
    UNIQUE (user_a, user_b),
    FOREIGN KEY (user_a) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (user_b) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    body TEXT NOT NULL,
    payload TEXT DEFAULT '',
    read_at TEXT,
    created_at TEXT NOT NULL DEFAULT {NOW_SQL},
    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS call_logs (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    caller_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'audio',
    status TEXT NOT NULL DEFAULT 'missed',
    duration INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT {NOW_SQL}
);

CREATE TABLE IF NOT EXISTS announcements (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    sent_to INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT {NOW_SQL}
);

CREATE INDEX IF NOT EXISTS idx_tracks_artist ON user_tracks (artist_id);
CREATE INDEX IF NOT EXISTS idx_tracks_user ON user_tracks (user_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_codes_user ON verification_codes (user_id, purpose);
"""


def _adapt(sql):
    return sql.replace("?", "%s")


@contextmanager
def connection():
    conn = psycopg2.connect(Config.DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    global _initialised
    with _lock:
        if _initialised:
            return
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
        _initialised = True


def query_all(sql, params=()):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_adapt(sql), params)
            return [dict(row) for row in cur.fetchall()]


def query_one(sql, params=()):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_adapt(sql), params)
            row = cur.fetchone()
            return dict(row) if row else None


def execute(sql, params=()):
    adapted = _adapt(sql)
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(adapted, params)
            if "RETURNING" in adapted.upper():
                row = cur.fetchone()
                return row[0] if row else None
            return cur.rowcount


def execute_many(sql, seq):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(_adapt(sql), seq)


def scalar(sql, params=(), default=0):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_adapt(sql), params)
            row = cur.fetchone()
            if row is None or row[0] is None:
                return default
            return row[0]
