import logging
import os
import sys
import threading
from datetime import timedelta
from sqlalchemy import event
from sqlalchemy.engine import Engine
from flask import Flask, request
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
    # Derived from the playlist cap so the two cannot drift apart: with the
    # old hardcoded 50 against a 100-track cap, a full playlist could be
    # downloaded but never zipped — it just returned 413 with no way out.
    from app.downloader import PLAYLIST_MAX_TRACKS
    app.config["PLAYLIST_ZIP_MAX_TRACKS"] = int(
        os.environ.get("PLAYLIST_ZIP_MAX_TRACKS", PLAYLIST_MAX_TRACKS)
    )

    # Session config (server-side cookie, 8h admin session)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # SameSite=None required for Auth0 cross-site OAuth callback to carry the session cookie.
    # Secure must be True when SameSite=None; SESSION_COOKIE_SECURE=true is set in Pi .env
    # (Cloudflare tunnel + ProxyFix ensure HTTPS is detected correctly).
    app.config["SESSION_COOKIE_SAMESITE"] = "None"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

    # Site URL (used for email links and logout redirect)
    app.config["SITE_URL"] = os.environ.get("SITE_URL", "https://yt2mp3.f1madrid.win")

    # Version / build info (injected at Docker build time)
    app.config["APP_VERSION"] = os.environ.get("APP_VERSION", "5.3.8")
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

    # SQLite tuning for concurrent readers/writers on Raspberry Pi.
    # WAL improves concurrency; busy_timeout reduces SQLITE_BUSY failures.
    #
    # foreign_keys is OFF by default in SQLite, which is how playlist_tracks
    # rows came to outlive the downloads they referenced and crash the player.
    # The constraints were declared all along, just never enforced. This is a
    # per-connection setting, so it reverts with this one line and a restart —
    # no data migration either way.
    @event.listens_for(Engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        try:
            if dbapi_connection.__class__.__module__.split(".")[0] != "sqlite3":
                return
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        except Exception:
            pass

    # ── Rate-limit exemption: local requests + admin sessions bypass limits ──
    @limiter.request_filter
    def _rate_limit_exempt():
        from app.auth_utils import _is_local_request
        from flask import session as _sess
        if _is_local_request():
            return True
        if _sess.get("is_admin"):
            return True
        return False

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
        from app.player_models import Playlist, PlaylistTrack, PlaylistShare, PlaylistMember, UserFeature, PlayEvent, LyricsCache, LyricsBlacklist, ApiToken  # noqa: F401
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

        # v4.8.0 — admin/enabled flags on users + drop WebAuthn tables
        for col_sql in (
            "ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0",
            "ALTER TABLE users ADD COLUMN is_enabled BOOLEAN DEFAULT 1",
        ):
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(col_sql))
                    conn.commit()
            except Exception:
                pass  # column already exists

        for drop_sql in (
            "DROP TABLE IF EXISTS webauthn_challenges",
            "DROP TABLE IF EXISTS webauthn_credentials",
            "DROP TABLE IF EXISTS admin_users",
        ):
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(drop_sql))
                    conn.commit()
            except Exception:
                pass

        # v4.9.0 — local password auth
        for col_sql in (
            "ALTER TABLE users ADD COLUMN password_hash TEXT",
        ):
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(col_sql))
                    conn.commit()
            except Exception:
                pass  # column already exists

        # v4.10.0 — collaborative playlists
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS playlist_members ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  playlist_id INTEGER NOT NULL,"
                    "  user_email VARCHAR(256) NOT NULL,"
                    "  role VARCHAR(16) NOT NULL DEFAULT 'editor',"
                    "  joined_at DATETIME,"
                    "  FOREIGN KEY (playlist_id) REFERENCES playlists(id),"
                    "  FOREIGN KEY (user_email) REFERENCES users(email),"
                    "  UNIQUE (playlist_id, user_email)"
                    ")"
                ))
                conn.commit()
        except Exception:
            pass

        for col_sql in (
            "ALTER TABLE playlist_tracks ADD COLUMN added_by VARCHAR(256)",
            "ALTER TABLE playlist_shares ADD COLUMN mode VARCHAR(16) DEFAULT 'view'",
        ):
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(col_sql))
                    conn.commit()
            except Exception:
                pass  # column already exists

        # v4.10.0 — seed playlist_members with owners for existing playlists
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "INSERT OR IGNORE INTO playlist_members (playlist_id, user_email, role, joined_at) "
                    "SELECT id, user_email, 'owner', created_at FROM playlists "
                    "WHERE user_email IS NOT NULL "
                    "AND user_email NOT IN (SELECT user_email FROM playlist_members WHERE playlist_id = playlists.id AND role = 'owner')"
                ))
                conn.commit()
        except Exception:
            pass

        # v4.12.0 — API tokens
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS api_tokens ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  user_email VARCHAR(256) NOT NULL,"
                    "  name VARCHAR(128) NOT NULL,"
                    "  token_hash VARCHAR(128) UNIQUE NOT NULL,"
                    "  token_prefix VARCHAR(8) NOT NULL,"
                    "  created_at DATETIME,"
                    "  last_used_at DATETIME,"
                    "  is_active BOOLEAN NOT NULL DEFAULT 1,"
                    "  FOREIGN KEY (user_email) REFERENCES users(email)"
                    ")"
                ))
                conn.commit()
        except Exception:
            pass

        # v5.0.0 — Playlist batch downloads
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS playlist_batches ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  batch_id VARCHAR(64) UNIQUE NOT NULL,"
                    "  created_at DATETIME,"
                    "  status VARCHAR(16) DEFAULT 'pending',"
                    "  user_email VARCHAR(256),"
                    "  youtube_url TEXT NOT NULL,"
                    "  playlist_title TEXT,"
                    "  track_count INTEGER DEFAULT 0,"
                    "  completed INTEGER DEFAULT 0,"
                    "  failed INTEGER DEFAULT 0,"
                    "  skipped INTEGER DEFAULT 0,"
                    "  app_playlist_id INTEGER,"
                    "  error_message TEXT,"
                    "  ip_address VARCHAR(64),"
                    "  fingerprint_hash VARCHAR(256),"
                    "  country_code VARCHAR(2),"
                    "  city VARCHAR(128),"
                    "  entries_json TEXT,"
                    "  FOREIGN KEY (user_email) REFERENCES users(email)"
                    ")"
                ))
                conn.commit()
        except Exception:
            pass
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE downloads ADD COLUMN batch_id VARCHAR(64)"
                ))
                conn.commit()
        except Exception:
            pass

        # v5.1.0 — store playlist entries in DB instead of session cookie
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE playlist_batches ADD COLUMN entries_json TEXT"
                ))
                conn.commit()
        except Exception:
            pass

        # v5.3.8 — drop columns the app stopped writing long ago.
        # Verified empty across every row before removal: these are leftovers
        # from a tracking-pixel experiment (Meta _fbp/_fbc, Google Analytics,
        # Instagram) and an older playlist implementation. fingerprint.py has
        # said "No cookie data is collected" for a while; the schema had not
        # caught up. Their absence also unblocks rebuilding this table, which
        # a naive rebuild from the model would otherwise have silently
        # dropped along with any data they held.
        _DEAD_COLUMNS = (
            "cookies_json", "fb_fbc", "fb_fbp",
            "ga_client", "ga_session", "ig_did", "playlist_url",
        )
        try:
            with db.engine.connect() as conn:
                present = {
                    row[1] for row in
                    conn.execute(text("PRAGMA table_info(downloads)")).fetchall()
                }
                for column in _DEAD_COLUMNS:
                    if column not in present:
                        continue
                    # Only drop what is genuinely unused — never assume.
                    used = conn.execute(text(
                        f"SELECT COUNT(*) FROM downloads "
                        f"WHERE {column} IS NOT NULL AND {column} != ''"
                    )).scalar()
                    if used:
                        logging.getLogger("app").warning(
                            "dead-column cleanup: %s holds %d values — kept",
                            column, used,
                        )
                        continue
                    conn.execute(text(f"ALTER TABLE downloads DROP COLUMN {column}"))
                    logging.getLogger("app").info("dropped unused column downloads.%s", column)
                conn.commit()
        except Exception as exc:
            logging.getLogger("app").warning("dead-column cleanup skipped: %s", exc)

        # v5.3.8 — indexes the models declare but the database never got:
        # those columns were added by ALTER TABLE, which does not create them.
        # video_id matters most — the dedup check queries it on every download.
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS ix_downloads_user_email ON downloads (user_email)",
            "CREATE INDEX IF NOT EXISTS ix_downloads_video_id   ON downloads (video_id)",
            "CREATE INDEX IF NOT EXISTS ix_downloads_audio_hash ON downloads (audio_hash)",
            "CREATE INDEX IF NOT EXISTS ix_downloads_batch_id   ON downloads (batch_id)",
        ):
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(index_sql))
                    conn.commit()
            except Exception:
                pass

        # v5.3.9 — add the two foreign keys that ALTER TABLE could never
        # attach. Rebuilds those tables, so it backs up first and rolls back
        # rather than committing anything diminished. Idempotent.
        try:
            from app.schema_migrations import ensure_user_foreign_keys
            ensure_user_foreign_keys(db)
        except Exception as exc:
            logging.getLogger("app").error(
                "schema migration failed, database left unchanged: %s", exc
            )

        # v5.3.4 — clear rows left pointing at downloads deleted before the
        # delete paths went through app.downloads_service. Idempotent.
        try:
            from app.downloads_service import purge_orphan_references
            purge_orphan_references()
        except Exception as exc:
            db.session.rollback()
            logging.getLogger("app").warning("orphan cleanup failed: %s", exc)

    # ── Startup banner ────────────────────────────────────────────────────────
    # Printed on every boot so `docker logs` answers the two questions that
    # cost real time when something looks wrong: which build is actually
    # running, and whether geolocation resolved a database.
    with app.app_context():
        from app.geo import _resolve_db_path
        geoip_status = _resolve_db_path() or "disabled (no database found)"
    logging.getLogger("app").info(
        "yt2mp3 %s (%s) starting — downloads=%s geoip=%s",
        app.config["APP_VERSION"],
        app.config["GIT_COMMIT"],
        app.config["DOWNLOAD_DIR"],
        geoip_status,
    )

    from app.routes import bp
    from app.admin_routes import admin_bp
    from app.player_routes import player_bp
    from app.auth_routes import auth_bp
    from app.mis_descargas_routes import mis_bp
    from app.settings_routes import settings_bp
    app.register_blueprint(bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(player_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(mis_bp)
    app.register_blueprint(settings_bp)

    # ── Security headers ──────────────────────────────────────────────────────
    # Built from what the front end actually loads, audited rather than guessed:
    #   scripts  — same origin, plus Chart.js on jsdelivr for /db/analytics
    #   styles   — same origin, plus Google Fonts stylesheets
    #   fonts    — Google Fonts
    #   images   — any https host: cover art comes from iTunes, Deezer, Genius
    #              and YouTube, and the set is not knowable in advance
    #   audio    — same origin only (/player/stream/<job_id>)
    #   connect  — same origin only; nothing fetches cross-origin
    #
    # 'unsafe-inline' is still required for scripts: 93 inline event handlers
    # and 10 inline <script> blocks remain, and CSP blocks every inline handler
    # unless it is allowed — a nonce cannot rescue them. So this does not stop
    # injected inline script yet. What it does stop is loading script from an
    # attacker's host, framing the site, and posting forms off-origin. Removing
    # those handlers is the prerequisite for a strict policy.
    _CSP = "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: https:",
        "media-src 'self' blob:",
        "connect-src 'self'",
        "worker-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])

    # CSP_MODE: enforce (default) | report-only | off. An escape hatch that
    # does not need a code change if the policy turns out to block something.
    csp_mode = os.environ.get("CSP_MODE", "enforce").lower()

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        # cache-manager.js registers /static/sw.js with scope '/', but a worker
        # served from /static/ may only claim /static/ unless the response says
        # otherwise — so registration has always been rejected and the offline
        # audio cache never worked.
        if request.path == "/static/sw.js":
            response.headers.setdefault("Service-Worker-Allowed", "/")
        if csp_mode == "enforce":
            response.headers.setdefault("Content-Security-Policy", _CSP)
        elif csp_mode == "report-only":
            response.headers.setdefault("Content-Security-Policy-Report-Only", _CSP)
        return response

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
