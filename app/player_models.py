import uuid
import hashlib
import secrets
from datetime import datetime, timezone
from app import db


class Playlist(db.Model):
    __tablename__ = "playlists"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_added = db.Column(db.DateTime, nullable=True)  # updated when a track is added

    # Auth user (v3.0.0) — NULL = local/admin-created playlist
    user_email = db.Column(db.String(256), nullable=True, index=True)

    tracks = db.relationship(
        "PlaylistTrack",
        back_populates="playlist",
        order_by="PlaylistTrack.position",
        cascade="all, delete-orphan",
    )


class PlaylistTrack(db.Model):
    __tablename__ = "playlist_tracks"

    id          = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id"), nullable=False)
    job_id      = db.Column(db.String(64), db.ForeignKey("downloads.job_id"), nullable=False)
    position    = db.Column(db.Integer, nullable=False, default=0)
    added_by    = db.Column(db.String(256), nullable=True)  # v4.10.0 — email of who added

    playlist = db.relationship("Playlist", back_populates="tracks")
    download = db.relationship("Download")


class PlaylistShare(db.Model):
    __tablename__ = "playlist_shares"

    id          = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id"), nullable=False)
    token       = db.Column(db.String(36), unique=True, nullable=False,
                            default=lambda: str(uuid.uuid4()))
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    mode        = db.Column(db.String(16), nullable=False, default="view")  # v4.10.0 — 'view' | 'collaborate'

    playlist = db.relationship("Playlist")


class PlaylistMember(db.Model):
    """Collaborative playlist membership (v4.10.0)."""
    __tablename__ = "playlist_members"

    id          = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id"), nullable=False)
    user_email  = db.Column(db.String(256), db.ForeignKey("users.email"), nullable=False)
    role        = db.Column(db.String(16), nullable=False, default="editor")  # 'owner' | 'editor'
    joined_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("playlist_id", "user_email", name="uq_playlist_member"),
    )

    playlist = db.relationship("Playlist", backref=db.backref("members", cascade="all, delete-orphan"))
    user     = db.relationship("User")


class UserFeature(db.Model):
    """Per-user feature flags (set by admin)."""
    __tablename__ = "user_features"

    user_email     = db.Column(db.String(256), db.ForeignKey("users.email"),
                               primary_key=True)
    lyrics_enabled = db.Column(db.Boolean, default=False, nullable=False)
    share_enabled  = db.Column(db.Boolean, default=False, nullable=False)


class PlayEvent(db.Model):
    """One row per confirmed play (>30 s listened or track ended)."""
    __tablename__ = "play_events"

    id             = db.Column(db.Integer, primary_key=True)
    user_email     = db.Column(db.String(256), db.ForeignKey("users.email"),
                               nullable=False, index=True)
    job_id         = db.Column(db.String(64), db.ForeignKey("downloads.job_id"),
                               nullable=False)
    played_at      = db.Column(db.DateTime,
                               default=lambda: datetime.now(timezone.utc),
                               nullable=False)
    seconds_played = db.Column(db.Integer, nullable=False, default=0)


class LyricsCache(db.Model):
    """Cached lyrics keyed by YouTube video_id."""
    __tablename__ = "lyrics_cache"

    video_id = db.Column(db.String(32), primary_key=True)
    source   = db.Column(db.String(16), nullable=False)   # 'lrclib' | 'ovh'
    synced   = db.Column(db.Boolean, default=False, nullable=False)
    content  = db.Column(db.Text, nullable=True)   # LRC or plain
    plain    = db.Column(db.Text, nullable=True)   # always plain fallback
    fetched_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class LyricsBlacklist(db.Model):
    """Blacklisted lyrics sources per video_id — admin-rejected entries won't be re-fetched from the same source."""
    __tablename__ = "lyrics_blacklist"

    id       = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.String(32), nullable=False, index=True)
    source   = db.Column(db.String(16), nullable=False)   # 'lrclib' | 'ovh' | '*' = all
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ApiToken(db.Model):
    """Per-user API tokens for programmatic access (v4.12.0).

    The raw token (``yt2_<32hex>``) is shown to the user exactly once at creation.
    Only the SHA-256 hash is stored.  ``token_prefix`` keeps the first 8 chars
    (``yt2_xxxx``) for display purposes.
    """
    __tablename__ = "api_tokens"

    id          = db.Column(db.Integer, primary_key=True)
    user_email  = db.Column(db.String(256), db.ForeignKey("users.email"), nullable=False, index=True)
    name        = db.Column(db.String(128), nullable=False)
    token_hash  = db.Column(db.String(128), unique=True, nullable=False)
    token_prefix = db.Column(db.String(8), nullable=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at = db.Column(db.DateTime, nullable=True)
    is_active   = db.Column(db.Boolean, default=True, nullable=False)

    user = db.relationship("User")

    @staticmethod
    def generate():
        """Return (raw_token, token_hash, token_prefix)."""
        raw = "yt2_" + secrets.token_hex(16)
        h = hashlib.sha256(raw.encode()).hexdigest()
        prefix = raw[:8]
        return raw, h, prefix
