from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from config import Config
from modules import auth, database, matching, messaging, realtime

bp = Blueprint("chat", __name__)


@bp.get("/messages")
@auth.onboarding_required
def inbox_page():
    user = auth.current_user()
    conversations = messaging.list_conversations(user["id"])
    online = realtime.online_ids()
    for conversation in conversations:
        conversation["online"] = conversation["other_id"] in online
    return render_template("messages.html", user=user, conversations=conversations)


@bp.get("/messages/<int:conversation_id>")
@auth.onboarding_required
def chat_page(conversation_id):
    user = auth.current_user()
    try:
        conversation = messaging.get_conversation(conversation_id, user["id"])
    except messaging.MessagingError:
        return redirect(url_for("chat.inbox_page"))

    other_id = messaging.partner_id(conversation, user["id"])
    other = database.query_one("SELECT * FROM users WHERE id = ?", (other_id,))
    messaging.mark_read(conversation_id, user["id"])

    return render_template(
        "chat.html",
        user=user,
        other=other,
        conversation=conversation,
        messages=messaging.history(conversation_id),
        online=realtime.is_online(other_id),
        ice_servers=Config.ICE_SERVERS,
        shared=matching.top_tracks_of(other_id, 3),
    )


@bp.post("/api/conversations")
@auth.onboarding_required
def create_conversation():
    user = auth.current_user()
    data = request.get_json(silent=True) or {}
    try:
        target_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Utilisateur invalide."}), 400
    try:
        conversation_id = messaging.open_conversation(user["id"], target_id)
    except messaging.MessagingError as error:
        return jsonify({"ok": False, "error": str(error)}), 403
    return jsonify({"ok": True, "conversation_id": conversation_id, "redirect": url_for("chat.chat_page", conversation_id=conversation_id)})


@bp.get("/api/conversations")
@auth.onboarding_required
def list_api():
    user = auth.current_user()
    conversations = messaging.list_conversations(user["id"])
    online = realtime.online_ids()
    for conversation in conversations:
        conversation["online"] = conversation["other_id"] in online
    return jsonify({"ok": True, "conversations": conversations, "unread": messaging.unread_total(user["id"])})


@bp.get("/api/conversations/<int:conversation_id>/messages")
@auth.onboarding_required
def history_api(conversation_id):
    user = auth.current_user()
    try:
        messaging.get_conversation(conversation_id, user["id"])
    except messaging.MessagingError as error:
        return jsonify({"ok": False, "error": str(error)}), 404
    before = request.args.get("before")
    try:
        before_id = int(before) if before else None
    except ValueError:
        before_id = None
    return jsonify({"ok": True, "messages": messaging.history(conversation_id, before_id)})


@bp.get("/api/unread")
@auth.onboarding_required
def unread_api():
    user = auth.current_user()
    return jsonify({"ok": True, "unread": messaging.unread_total(user["id"])})
