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
