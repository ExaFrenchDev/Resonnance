import threading
from collections import defaultdict

from flask import request, session
from flask_socketio import emit, join_room, leave_room

from modules import database, mailer, messaging

_sockets = defaultdict(set)
_sockets_lock = threading.Lock()


def user_room(user_id):
    return f"user:{user_id}"


def conversation_room(conversation_id):
    return f"conv:{conversation_id}"


def is_online(user_id):
    with _sockets_lock:
        return bool(_sockets.get(user_id))


def online_ids():
    with _sockets_lock:
        return {user_id for user_id, sids in _sockets.items() if sids}


def push_to_user(user_id, event, payload):
    from app import socketio

    socketio.emit(event, payload, to=user_room(user_id))


def _session_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return database.query_one("SELECT id, username, display_name, email, avatar_seed FROM users WHERE id = ?", (user_id,))


def register(socketio, base_url_getter):
    @socketio.on("connect")
    def handle_connect():
        user = _session_user()
        if not user:
            return False
        with _sockets_lock:
            _sockets[user["id"]].add(request.sid)
        join_room(user_room(user["id"]))
        database.execute("UPDATE users SET last_seen = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = ?", (user["id"],))
        emit("presence:self", {"user_id": user["id"], "online": True})
        socketio.emit("presence:update", {"user_id": user["id"], "online": True})
        return None

    @socketio.on("disconnect")
    def handle_disconnect():
        user_id = session.get("user_id")
        if not user_id:
            return
        with _sockets_lock:
            _sockets[user_id].discard(request.sid)
            still_online = bool(_sockets[user_id])
        if not still_online:
            database.execute("UPDATE users SET last_seen = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = ?", (user_id,))
            socketio.emit("presence:update", {"user_id": user_id, "online": False})

    @socketio.on("conversation:join")
    def handle_join(data):
        user = _session_user()
        if not user:
            return
        try:
            conversation = messaging.get_conversation(int(data.get("conversation_id", 0)), user["id"])
        except (messaging.MessagingError, ValueError, TypeError):
            emit("error:toast", {"message": "Conversation inaccessible."})
            return
        join_room(conversation_room(conversation["id"]))
        messaging.mark_read(conversation["id"], user["id"])
        other = messaging.partner_id(conversation, user["id"])
        emit("conversation:ready", {"conversation_id": conversation["id"], "partner_online": is_online(other)})
        socketio.emit(
            "message:read",
            {"conversation_id": conversation["id"], "reader_id": user["id"]},
            to=conversation_room(conversation["id"]),
        )

    @socketio.on("conversation:leave")
    def handle_leave(data):
        try:
            leave_room(conversation_room(int(data.get("conversation_id", 0))))
        except (ValueError, TypeError):
            pass

    @socketio.on("message:send")
    def handle_message(data):
        user = _session_user()
        if not user:
            return
        try:
            conversation = messaging.get_conversation(int(data.get("conversation_id", 0)), user["id"])
            message = messaging.post(conversation["id"], user["id"], data.get("body", ""), data.get("kind", "text"), data.get("payload", ""))
        except (messaging.MessagingError, ValueError, TypeError) as error:
            emit("error:toast", {"message": str(error)})
            return

        payload = {
            "id": message["id"],
            "conversation_id": conversation["id"],
            "sender_id": user["id"],
            "sender_name": user["display_name"] or user["username"],
            "kind": message["kind"],
            "body": message["body"],
            "payload": message["payload"],
            "created_at": message["created_at"],
            "client_ref": data.get("client_ref"),
        }
        socketio.emit("message:new", payload, to=conversation_room(conversation["id"]))

        other_id = messaging.partner_id(conversation, user["id"])
        socketio.emit("inbox:bump", payload, to=user_room(other_id))
        if not is_online(other_id):
            other = database.query_one("SELECT email, username, display_name FROM users WHERE id = ?", (other_id,))
            if other:
                mailer.send_new_message_alert(
                    other["email"],
                    other["display_name"] or other["username"],
                    user["display_name"] or user["username"],
                    base_url_getter(),
                )

    @socketio.on("typing")
    def handle_typing(data):
        user = _session_user()
        if not user:
            return
        try:
            conversation_id = int(data.get("conversation_id", 0))
        except (ValueError, TypeError):
            return
        emit(
            "typing",
            {"conversation_id": conversation_id, "user_id": user["id"], "state": bool(data.get("state"))},
            to=conversation_room(conversation_id),
            include_self=False,
        )

    @socketio.on("call:invite")
    def handle_call_invite(data):
        user = _session_user()
        if not user:
            return
        try:
            conversation = messaging.get_conversation(int(data.get("conversation_id", 0)), user["id"])
        except (messaging.MessagingError, ValueError, TypeError):
            return
        other_id = messaging.partner_id(conversation, user["id"])
        mode = "video" if data.get("mode") == "video" else "audio"
        if not is_online(other_id):
            messaging.log_call(conversation["id"], user["id"], mode, "missed")
            emit("call:unavailable", {"conversation_id": conversation["id"], "reason": "Personne indisponible pour le moment."})
            return
        socketio.emit(
            "call:incoming",
            {
                "conversation_id": conversation["id"],
                "from_id": user["id"],
                "from_name": user["display_name"] or user["username"],
                "avatar_seed": user["avatar_seed"],
                "mode": mode,
            },
            to=user_room(other_id),
        )

    @socketio.on("call:signal")
    def handle_call_signal(data):
        user = _session_user()
        if not user:
            return
        try:
            conversation = messaging.get_conversation(int(data.get("conversation_id", 0)), user["id"])
        except (messaging.MessagingError, ValueError, TypeError):
            return
        other_id = messaging.partner_id(conversation, user["id"])
        socketio.emit(
            "call:signal",
            {
                "conversation_id": conversation["id"],
                "from_id": user["id"],
                "signal": data.get("signal"),
                "mode": data.get("mode", "audio"),
            },
            to=user_room(other_id),
        )

    @socketio.on("call:accept")
    def handle_call_accept(data):
        user = _session_user()
        if not user:
            return
        try:
            conversation = messaging.get_conversation(int(data.get("conversation_id", 0)), user["id"])
        except (messaging.MessagingError, ValueError, TypeError):
            return
        other_id = messaging.partner_id(conversation, user["id"])
        socketio.emit("call:accepted", {"conversation_id": conversation["id"], "from_id": user["id"]}, to=user_room(other_id))

    @socketio.on("call:end")
    def handle_call_end(data):
        user = _session_user()
        if not user:
            return
        try:
            conversation = messaging.get_conversation(int(data.get("conversation_id", 0)), user["id"])
        except (messaging.MessagingError, ValueError, TypeError):
            return
        other_id = messaging.partner_id(conversation, user["id"])
        duration = int(data.get("duration", 0) or 0)
        status = data.get("status", "ended")
        messaging.log_call(conversation["id"], user["id"], data.get("mode", "audio"), status, duration)
        if duration > 0:
            minutes, seconds = divmod(duration, 60)
            messaging.post(conversation["id"], user["id"], f"Appel terminé — {minutes:02d}:{seconds:02d}", kind="call")
        socketio.emit(
            "call:ended",
            {"conversation_id": conversation["id"], "from_id": user["id"], "duration": duration},
            to=user_room(other_id),
        )
