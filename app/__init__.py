import logging
import os
import sys
import threading
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

    # ── Logging — route app.* logs to stdout so they appear in docker logs ──
    if not app.debug and not logging.getLogger("app").handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        logging.getLogger("app").addHandler(handler)
        logging.getLogger("app").setLevel(logging.INFO)

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

    # Version / build info (injected at Docker build time)
    app.config["APP_VERSION"] = os.environ.get("APP_VERSION", "1.6.5")
    app.config["GIT_COMMIT"]  = os.environ.get("GIT_COMMIT", "dev")
    app.config["REPO_URL"]    = "https://github.com/davic80/yt2mp3"

    # Admin panel auto-refresh interval in seconds (0 = disabled)
    app.config["ADMIN_REFRESH_INTERVAL"] = int(os.environ.get("ADMIN_REFRESH_INTERVAL", 300))

    # Ensure dirs exist
    os.makedirs(app.config["DOWNLOAD_DIR"], exist_ok=True)
    os.makedirs(os.path.dirname(
        app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:////", "/").replace("sqlite:///", "")
    ), exist_ok=True)

    db.init_app(app)
    limiter.init_app(app)

    with app.app_context():
        from sqlalchemy import text
        from app.models import Download  # noqa: F401
        from app.admin_models import AdminUser, WebAuthnCredential, WebAuthnChallenge  # noqa: F401
        db.create_all()

        # ── Inline migrations: add new columns if they don't exist yet ──
        for col_sql in (
            "ALTER TABLE downloads ADD COLUMN hardware_model VARCHAR(256)",
            "ALTER TABLE downloads ADD COLUMN identity_hash VARCHAR(16)",
            "ALTER TABLE downloads ADD COLUMN bot_score INTEGER",
            "ALTER TABLE downloads ADD COLUMN country_code VARCHAR(2)",
            "ALTER TABLE downloads ADD COLUMN city VARCHAR(128)",
        ):
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(col_sql))
                    conn.commit()
            except Exception:
                pass  # column already exists — safe to ignore

    from app.routes import bp
    from app.admin_routes import admin_bp
    app.register_blueprint(bp)
    app.register_blueprint(admin_bp)

    @app.context_processor
    def inject_build_info():
        return {
            "version":          app.config["APP_VERSION"],
            "commit":           app.config["GIT_COMMIT"],
            "repo_url":         app.config["REPO_URL"],
            "refresh_interval": app.config["ADMIN_REFRESH_INTERVAL"],
        }

    # ── Background migration: fill hardware_model / identity_hash for old rows ──
    def _migrate_hardware():
        from app.hardware_parser import detect_hardware, compute_identity_hash
        logger = logging.getLogger("app")
        with app.app_context():
            try:
                rows = Download.query.filter(
                    (Download.hardware_model == None) | (Download.identity_hash == None),  # noqa: E711
                    Download.fingerprint_components != None,  # noqa: E711
                ).all()
                if not rows:
                    return
                updated = 0
                for r in rows:
                    if not r.hardware_model:
                        r.hardware_model = detect_hardware(r.fingerprint_components)
                    if not r.identity_hash:
                        r.identity_hash = compute_identity_hash(r.fingerprint_components)
                    updated += 1
                db.session.commit()
                logger.info("hardware migration: updated %d rows", updated)
            except Exception as exc:
                logger.warning("hardware migration failed: %s", exc)

    threading.Thread(target=_migrate_hardware, daemon=True).start()

    # ── Background migration: fill country_code / city for old rows ──
    def _migrate_geo():
        from app.geo import geolocate
        logger = logging.getLogger("app")
        with app.app_context():
            try:
                rows = Download.query.filter(
                    Download.ip_address != None,       # noqa: E711
                    Download.country_code == None,     # noqa: E711
                ).all()
                if not rows:
                    return
                updated = 0
                for r in rows:
                    geo = geolocate(r.ip_address)
                    if geo["country_code"] or geo["city"]:
                        r.country_code = geo["country_code"]
                        r.city         = geo["city"]
                        updated += 1
                if updated:
                    db.session.commit()
                logger.info("geo migration: updated %d rows", updated)
            except Exception as exc:
                logger.warning("geo migration failed: %s", exc)

    threading.Thread(target=_migrate_geo, daemon=True).start()

    return app
