from config import Config  # noqa: F401
from modules import database, matching


class MessagingError(Exception):
    pass


def _pair(user_a, user_b):
    return (user_a, user_b) if user_a < user_b else (user_b, user_a)


def can_talk(user_a, user_b):
    if user_a == user_b:
        return False, 0
    score = matching.score_between(user_a, user_b)
    mutual = database.scalar(
        """SELECT COUNT(*) FROM profile_likes a
           JOIN profile_likes b ON a.from_id = b.to_id AND a.to_id = b.from_id
           WHERE a.from_id = ? AND a.to_id = ?""",
        (user_a, user_b),
    )
    return bool(mutual), score


def open_conversation(user_a, user_b, enforce=True):
    allowed, score = can_talk(user_a, user_b)
    if enforce and not allowed:
        raise MessagingError("Il faut que vous vous soyez likés tous les deux pour ouvrir la discussion.")
    left, right = _pair(user_a, user_b)
    existing = database.query_one(
        "SELECT * FROM conversations WHERE user_a = ? AND user_b = ?", (left, right)
    )
    if existing:
        database.execute("UPDATE conversations SET score = ? WHERE id = ?", (score, existing["id"]))
        return existing["id"]
    return database.execute(
        "INSERT INTO conversations (user_a, user_b, score) VALUES (?, ?, ?) RETURNING id", (left, right, score)
    )


def get_conversation(conversation_id, user_id):
    conversation = database.query_one(
        "SELECT * FROM conversations WHERE id = ? AND (user_a = ? OR user_b = ?)",
        (conversation_id, user_id, user_id),
    )
    if not conversation:
        raise MessagingError("Conversation introuvable.")
    return conversation


def partner_id(conversation, user_id):
    return conversation["user_b"] if conversation["user_a"] == user_id else conversation["user_a"]


def list_conversations(user_id):
    rows = database.query_all(
        """SELECT c.id, c.score, c.updated_at,
                  u.id AS other_id, u.username, u.display_name, u.avatar_seed, u.last_seen,
                  (SELECT body FROM messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_body,
                  (SELECT kind FROM messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_kind,
                  (SELECT sender_id FROM messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_sender,
                  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id AND m.sender_id != ? AND m.read_at IS NULL) AS unread
           FROM conversations c
           JOIN users u ON u.id = CASE WHEN c.user_a = ? THEN c.user_b ELSE c.user_a END
           WHERE c.user_a = ? OR c.user_b = ?
           ORDER BY c.updated_at DESC""",
        (user_id, user_id, user_id, user_id),
    )
    for row in rows:
        row["display_name"] = row["display_name"] or row["username"]
    return rows


def history(conversation_id, before_id=None, limit=60):
    if before_id:
        rows = database.query_all(
            """SELECT * FROM messages WHERE conversation_id = ? AND id < ?
               ORDER BY id DESC LIMIT ?""",
            (conversation_id, before_id, limit),
        )
    else:
        rows = database.query_all(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
    return list(reversed(rows))


def post(conversation_id, sender_id, body, kind="text", payload=""):
    body = (body or "").strip()
    if not body:
        raise MessagingError("Message vide.")
    if len(body) > 2000:
        raise MessagingError("Message trop long (2000 caractères maximum).")
    message_id = database.execute(
        "INSERT INTO messages (conversation_id, sender_id, kind, body, payload) VALUES (?, ?, ?, ?, ?) RETURNING id",
        (conversation_id, sender_id, kind, body, payload),
    )
    database.execute("UPDATE conversations SET updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = ?", (conversation_id,))
    return database.query_one("SELECT * FROM messages WHERE id = ?", (message_id,))


def mark_read(conversation_id, reader_id):
    database.execute(
        "UPDATE messages SET read_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE conversation_id = ? AND sender_id != ? AND read_at IS NULL",
        (conversation_id, reader_id),
    )


def unread_total(user_id):
    return database.scalar(
        """SELECT COUNT(*) FROM messages m
           JOIN conversations c ON c.id = m.conversation_id
           WHERE (c.user_a = ? OR c.user_b = ?) AND m.sender_id != ? AND m.read_at IS NULL""",
        (user_id, user_id, user_id),
    )


def log_call(conversation_id, caller_id, mode, status, duration=0):
    return database.execute(
        "INSERT INTO call_logs (conversation_id, caller_id, mode, status, duration) VALUES (?, ?, ?, ?, ?)",
        (conversation_id, caller_id, mode, status, duration),
    )
