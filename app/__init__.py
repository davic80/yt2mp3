import os
from datetime import timedelta
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="../static")

    # Core config
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:////app/database/yt2mp3.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["DOWNLOAD_DIR"] = os.environ.get("DOWNLOAD_DIR", "/app/downloads")
    app.config["RATE_LIMIT_PER_HOUR"] = os.environ.get("RATE_LIMIT_PER_HOUR", "10")
    app.config["RATE_LIMIT_PER_MINUTE"] = os.environ.get("RATE_LIMIT_PER_MINUTE", "3")

    # Session config (server-side cookie, 8h admin session)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # In production behind Cloudflare tunnel, cookies should be secure
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

    # WebAuthn config
    app.config["WEBAUTHN_RP_ID"] = os.environ.get("WEBAUTHN_RP_ID", "localhost")
    app.config["WEBAUTHN_RP_NAME"] = os.environ.get("WEBAUTHN_RP_NAME", "yt2mp3 admin")
    app.config["WEBAUTHN_ORIGIN"] = os.environ.get("WEBAUTHN_ORIGIN", "http://localhost:5000")

    # Ensure dirs exist
    os.makedirs(app.config["DOWNLOAD_DIR"], exist_ok=True)
    os.makedirs(os.path.dirname(
        app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:////", "/").replace("sqlite:///", "")
    ), exist_ok=True)

    db.init_app(app)
    limiter.init_app(app)

    with app.app_context():
        from app.models import Download  # noqa: F401
        from app.admin_models import AdminUser, WebAuthnCredential, WebAuthnChallenge  # noqa: F401
        db.create_all()

    from app.routes import bp
    from app.admin_routes import admin_bp
    app.register_blueprint(bp)
    app.register_blueprint(admin_bp)

    return app
