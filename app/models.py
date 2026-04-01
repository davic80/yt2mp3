from datetime import datetime, timezone
from app import db


class User(db.Model):
    """User — created via Google OAuth.

    The password_hash and provider columns are retained for backward compatibility
    with existing databases but are no longer used for authentication (v4.12.0).
    """
    __tablename__ = "users"

    email         = db.Column(db.String(256), primary_key=True)
    name          = db.Column(db.String(256), nullable=True)
    picture       = db.Column(db.Text, nullable=True)       # avatar URL from provider
    provider      = db.Column(db.String(16), nullable=True) # 'google' | 'local'
    password_hash = db.Column(db.Text, nullable=True)       # PBKDF2 hash (local users only)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_admin      = db.Column(db.Boolean, default=False)
    is_enabled    = db.Column(db.Boolean, default=True)

    downloads = db.relationship("Download", back_populates="user", lazy="dynamic")


class Download(db.Model):
    __tablename__ = "downloads"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(16), default="pending")  # pending, done, error
    error_message = db.Column(db.Text, nullable=True)

    # Content
    youtube_url = db.Column(db.Text, nullable=False)
    file_path = db.Column(db.Text, nullable=True)
    file_name = db.Column(db.Text, nullable=True)
    title = db.Column(db.Text, nullable=True)

    # Network
    ip_address = db.Column(db.String(64), nullable=True)
    referrer = db.Column(db.Text, nullable=True)
    country_code = db.Column(db.String(2), nullable=True)   # ISO 3166-1 alpha-2
    city = db.Column(db.String(128), nullable=True)

    # User-Agent (raw + parsed)
    user_agent_raw = db.Column(db.Text, nullable=True)
    ua_browser = db.Column(db.String(128), nullable=True)
    ua_browser_version = db.Column(db.String(64), nullable=True)
    ua_os = db.Column(db.String(128), nullable=True)
    ua_os_version = db.Column(db.String(64), nullable=True)
    ua_device = db.Column(db.String(64), nullable=True)  # Mobile / PC / Tablet
    ua_is_mobile = db.Column(db.Boolean, default=False)
    ua_is_bot = db.Column(db.Boolean, default=False)
    accept_language = db.Column(db.String(256), nullable=True)

    # Browser fingerprint (client-side JS)
    fingerprint_hash = db.Column(db.String(256), nullable=True)
    fingerprint_components = db.Column(db.Text, nullable=True)  # JSON string

    # Hardware inference (v1.4.0)
    hardware_model = db.Column(db.String(256), nullable=True)  # e.g. "Apple M1 Pro · MacBook Pro"
    identity_hash  = db.Column(db.String(16),  nullable=True)  # 8-char stable device hash

    # Bot score (v1.5.0) — 0-100 heuristic
    bot_score = db.Column(db.Integer, nullable=True)

    # File size in bytes (v1.7.0)
    file_size = db.Column(db.Integer, nullable=True)

    # Player (v2.0.0)
    is_favorite = db.Column(db.Boolean, default=False, nullable=False)

    # Auth user (v3.0.0) — NULL = anonymous download
    user_email = db.Column(db.String(256), db.ForeignKey("users.email"), nullable=True, index=True)
    user = db.relationship("User", back_populates="downloads")

    # Deduplication (v3.2.0)
    # video_id: YouTube video ID extracted from the URL (e.g. "dQw4w9WgXcQ")
    # audio_hash: SHA-256 hex digest of the MP3 file, computed after download
    # Multiple Download rows may share the same file_path when deduplicated.
    video_id   = db.Column(db.String(32),  nullable=True, index=True)
    audio_hash = db.Column(db.String(64),  nullable=True, index=True)

    # Artwork (v4.6.3) — cached cover art URL from iTunes/Deezer/YouTube
    artwork_url         = db.Column(db.Text,    nullable=True)
    artwork_blacklisted = db.Column(db.Boolean, default=False, nullable=False)

    # Playlist batch (v5.0.0) — links to PlaylistBatch when downloaded as part of a YT playlist
    batch_id = db.Column(db.String(64), nullable=True, index=True)

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "status": self.status,
            "title": self.title,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "error_message": self.error_message,
        }


class PlaylistBatch(db.Model):
    """A YouTube playlist download batch (v5.0.0).

    Tracks the lifecycle of downloading an entire YouTube playlist.
    Individual tracks are linked via ``Download.batch_id``.
    """
    __tablename__ = "playlist_batches"

    id              = db.Column(db.Integer, primary_key=True)
    batch_id        = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status          = db.Column(db.String(16), default="pending")
    # statuses: pending → extracting → downloading → done | error

    user_email      = db.Column(db.String(256), db.ForeignKey("users.email"), nullable=True)

    # YouTube metadata (filled after extraction)
    youtube_url     = db.Column(db.Text, nullable=False)
    playlist_title  = db.Column(db.Text, nullable=True)
    track_count     = db.Column(db.Integer, default=0)

    # Progress counters
    completed       = db.Column(db.Integer, default=0)
    failed          = db.Column(db.Integer, default=0)
    skipped         = db.Column(db.Integer, default=0)  # deduped tracks

    # Result
    app_playlist_id = db.Column(db.Integer, nullable=True)  # playlists.id after auto-creation
    error_message   = db.Column(db.Text, nullable=True)

    # Visitor metadata (subset — for analytics)
    ip_address       = db.Column(db.String(64), nullable=True)
    fingerprint_hash = db.Column(db.String(256), nullable=True)
    country_code     = db.Column(db.String(2), nullable=True)
    city             = db.Column(db.String(128), nullable=True)
