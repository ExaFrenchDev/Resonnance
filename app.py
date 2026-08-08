import eventlet
eventlet.monkey_patch()

import os

from flask import Flask, jsonify, render_template, request, send_from_directory, url_for
from flask_socketio import SocketIO

from config import Config
from modules import auth, database, messaging, realtime
from routes import BLUEPRINTS

socketio = SocketIO()


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(Config)

    database.init_db()

    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

    @app.template_global()
    def asset_url(filename):
        path = os.path.join(app.static_folder, filename)
        try:
            version = int(os.path.getmtime(path))
        except OSError:
            version = 0
        return f"{url_for('static', filename=filename)}?v={version}"

    @app.context_processor
    def inject_globals():
        user = auth.current_user()
        return {
            "current_user": user,
            "unread_count": messaging.unread_total(user["id"]) if user and user["onboarding_step"] == "done" else 0,
            "app_name": "Resonance",
            "strong_score": Config.STRONG_MATCH,
        }

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.route("/sw.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        response.mimetype = "application/javascript"
        return response

    @app.route("/ping")
    def ping():
        return "pong", 200

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Ressource introuvable."}), 404
        return render_template("not_found.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception(error)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Erreur interne. Réessaie dans un instant."}), 500
        return render_template("not_found.html", message="Une erreur interne est survenue."), 500

    socketio.init_app(app, cors_allowed_origins="*", async_mode="eventlet", manage_session=False)
    realtime.register(socketio, lambda: request.url_root.rstrip("/") if request else "")

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    print(f"Resonance -> http://127.0.0.1:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
