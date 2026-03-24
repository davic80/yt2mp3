import uuid
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

    playlist = db.relationship("Playlist", back_populates="tracks")
    download = db.relationship("Download")


class PlaylistShare(db.Model):
    __tablename__ = "playlist_shares"

    id          = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id"), nullable=False)
    token       = db.Column(db.String(36), unique=True, nullable=False,
                            default=lambda: str(uuid.uuid4()))
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    playlist = db.relationship("Playlist")


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
