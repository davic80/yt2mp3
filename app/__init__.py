import os
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

    # Config
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:////app/database/yt2mp3.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["DOWNLOAD_DIR"] = os.environ.get("DOWNLOAD_DIR", "/app/downloads")
    app.config["RATE_LIMIT_PER_HOUR"] = os.environ.get("RATE_LIMIT_PER_HOUR", "10")
    app.config["RATE_LIMIT_PER_MINUTE"] = os.environ.get("RATE_LIMIT_PER_MINUTE", "3")

    # Ensure dirs exist
    os.makedirs(app.config["DOWNLOAD_DIR"], exist_ok=True)
    os.makedirs("/app/database", exist_ok=True)

    db.init_app(app)
    limiter.init_app(app)

    with app.app_context():
        from app.models import Download  # noqa: F401
        db.create_all()

    from app.routes import bp
    app.register_blueprint(bp)

    return app
