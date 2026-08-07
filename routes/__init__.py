from routes.auth_routes import bp as auth_bp
from routes.chat_routes import bp as chat_bp
from routes.match_routes import bp as match_bp
from routes.music_routes import bp as music_bp

BLUEPRINTS = [auth_bp, music_bp, match_bp, chat_bp]
