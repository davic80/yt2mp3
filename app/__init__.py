import logging
import os
import sys
import threading
from datetime import timedelta
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from authlib.integrations.flask_client import OAuth

db = SQLAlchemy()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)
oauth = OAuth()


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
    # SameSite=None required for Auth0 cross-site OAuth callback to carry the session cookie.
    # Secure must be True when SameSite=None; SESSION_COOKIE_SECURE=true is set in Pi .env
    # (Cloudflare tunnel + ProxyFix ensure HTTPS is detected correctly).
    app.config["SESSION_COOKIE_SAMESITE"] = "None"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

    # WebAuthn config
    app.config["WEBAUTHN_RP_ID"] = os.environ.get("WEBAUTHN_RP_ID", "localhost")
    app.config["WEBAUTHN_RP_NAME"] = os.environ.get("WEBAUTHN_RP_NAME", "yt2mp3 admin")
    app.config["WEBAUTHN_ORIGIN"] = os.environ.get("WEBAUTHN_ORIGIN", "http://localhost:5000")

    # Version / build info (injected at Docker build time)
    app.config["APP_VERSION"] = os.environ.get("APP_VERSION", "4.6.8")
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

    # ── Google OAuth (Authlib) ────────────────────────────────────────────────
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email"},
    )

    with app.app_context():
        from sqlalchemy import text
        from app.models import User, Download  # noqa: F401
        from app.admin_models import AdminUser, WebAuthnCredential, WebAuthnChallenge  # noqa: F401
        from app.player_models import Playlist, PlaylistTrack, PlaylistShare, UserFeature, PlayEvent, LyricsCache, LyricsBlacklist  # noqa: F401
        db.create_all()

        # ── Inline migrations: add new columns if they don't exist yet ──
        for col_sql in (
            "ALTER TABLE downloads ADD COLUMN hardware_model VARCHAR(256)",
            "ALTER TABLE downloads ADD COLUMN identity_hash VARCHAR(16)",
            "ALTER TABLE downloads ADD COLUMN bot_score INTEGER",
            "ALTER TABLE downloads ADD COLUMN country_code VARCHAR(2)",
            "ALTER TABLE downloads ADD COLUMN city VARCHAR(128)",
            "ALTER TABLE downloads ADD COLUMN file_size INTEGER",
            "ALTER TABLE downloads ADD COLUMN is_favorite BOOLEAN NOT NULL DEFAULT 0",
            # v3.0.0 — user association
            "ALTER TABLE downloads ADD COLUMN user_email VARCHAR(256)",
            "ALTER TABLE playlists ADD COLUMN user_email VARCHAR(256)",
            # v3.2.0 — deduplication
            "ALTER TABLE downloads ADD COLUMN video_id VARCHAR(32)",
            "ALTER TABLE downloads ADD COLUMN audio_hash VARCHAR(64)",
            # v4.6.3 — artwork cache
            "ALTER TABLE downloads ADD COLUMN artwork_url TEXT",
            "ALTER TABLE downloads ADD COLUMN artwork_blacklisted BOOLEAN NOT NULL DEFAULT 0",
        ):
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(col_sql))
                    conn.commit()
            except Exception:
                pass  # column already exists — safe to ignore

        # v4.3.0 — playlist shares (new table, use CREATE TABLE IF NOT EXISTS)
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS playlist_shares ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  playlist_id INTEGER NOT NULL,"
                    "  token VARCHAR(36) UNIQUE NOT NULL,"
                    "  created_at DATETIME,"
                    "  FOREIGN KEY (playlist_id) REFERENCES playlists(id)"
                    ")"
                ))
                conn.commit()
        except Exception:
            pass

        # v4.4.0 — user feature flags + play events
        for create_sql in (
            (
                "CREATE TABLE IF NOT EXISTS user_features ("
                "  user_email VARCHAR(256) PRIMARY KEY,"
                "  lyrics_enabled BOOLEAN NOT NULL DEFAULT 0,"
                "  FOREIGN KEY (user_email) REFERENCES users(email)"
                ")"
            ),
            (
                "CREATE TABLE IF NOT EXISTS play_events ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  user_email VARCHAR(256) NOT NULL,"
                "  job_id VARCHAR(64) NOT NULL,"
                "  played_at DATETIME NOT NULL,"
                "  seconds_played INTEGER NOT NULL DEFAULT 0,"
                "  FOREIGN KEY (user_email) REFERENCES users(email),"
                "  FOREIGN KEY (job_id) REFERENCES downloads(job_id)"
                ")"
            ),
            (
                "CREATE TABLE IF NOT EXISTS lyrics_cache ("
                "  video_id VARCHAR(32) PRIMARY KEY,"
                "  source VARCHAR(16) NOT NULL,"
                "  synced BOOLEAN NOT NULL DEFAULT 0,"
                "  content TEXT,"
                "  plain TEXT,"
                "  fetched_at DATETIME"
                ")"
            ),
        ):
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(create_sql))
                    conn.commit()
            except Exception:
                pass

        # v4.4.1 — share_enabled feature flag
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE user_features ADD COLUMN share_enabled BOOLEAN NOT NULL DEFAULT 0"
                ))
                conn.commit()
        except Exception:
            pass  # column already exists

    from app.routes import bp
    from app.admin_routes import admin_bp
    from app.player_routes import player_bp
    from app.auth_routes import auth_bp
    from app.mis_descargas_routes import mis_bp
    app.register_blueprint(bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(player_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(mis_bp)

    @app.context_processor
    def inject_build_info():
        return {
            "version":          app.config["APP_VERSION"],
            "commit":           app.config["GIT_COMMIT"],
            "repo_url":         app.config["REPO_URL"],
            "refresh_interval": app.config["ADMIN_REFRESH_INTERVAL"],
        }

    @app.context_processor
    def inject_is_local():
        from app.auth_utils import _is_local_request
        return {"is_local": _is_local_request()}

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

    # ── Background migration: fill file_size for old done rows ──
    def _migrate_file_size():
        logger = logging.getLogger("app")
        with app.app_context():
            try:
                rows = Download.query.filter(
                    Download.status == "done",
                    Download.file_path != None,   # noqa: E711
                    Download.file_size == None,   # noqa: E711
                ).all()
                if not rows:
                    return
                updated = 0
                for r in rows:
                    try:
                        r.file_size = os.path.getsize(r.file_path)
                        updated += 1
                    except OSError:
                        pass  # file deleted from disk — leave NULL
                if updated:
                    db.session.commit()
                logger.info("file_size migration: updated %d rows", updated)
            except Exception as exc:
                logger.warning("file_size migration failed: %s", exc)

    threading.Thread(target=_migrate_file_size, daemon=True).start()

    return app
