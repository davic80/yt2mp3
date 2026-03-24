#!/usr/bin/env python3
"""
seed_test_data.py — crea datos de prueba idempotentes en la BD.

Crea:
  - 2 usuarios (test1@example.com, test2@example.com)
  - 3 canciones para test1, 2 canciones para test2  (ficheros dummy en /tmp)
  - 1 playlist "Rock Clásico" para test1 con 2 de sus canciones
  - 1 playlist "Pop Hits" para test2 con sus 2 canciones
  - UserFeature: test1 → lyrics+share habilitados; test2 → solo share

Uso:
    python scripts/seed_test_data.py

Idempotente: vuelve a ejecutarlo sin efecto si los datos ya existen.
"""

import sys
import os
import uuid
import pathlib

# Añadir la raíz del proyecto al path para que 'app' sea importable
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT}/database/yt2mp3.db")
os.environ.setdefault("SECRET_KEY", "dev-secret-change-me")
os.environ.setdefault("DOWNLOAD_DIR", str(ROOT / "downloads"))

from app import create_app, db
from app.models import User, Download
from app.player_models import Playlist, PlaylistTrack, UserFeature

USERS = [
    {
        "email": "test1@example.com",
        "name": "Usuario Test 1",
        "picture": None,
        "provider": "google",
    },
    {
        "email": "test2@example.com",
        "name": "Usuario Test 2",
        "picture": None,
        "provider": "google",
    },
]

TRACKS = [
    # test1 – 3 canciones
    {
        "job_id": "test1-track-001",
        "user_email": "test1@example.com",
        "title": "Bohemian Rhapsody",
        "file_name": "bohemian_rhapsody.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=fJ9rUzIMcZQ",
        "video_id": "fJ9rUzIMcZQ",
        "file_size": 8_500_000,
        "is_favorite": True,
        "artwork_url": "https://i.scdn.co/image/ab67616d0000b273ce4f1737bc8a646c8c4bd25a",
    },
    {
        "job_id": "test1-track-002",
        "user_email": "test1@example.com",
        "title": "Stairway to Heaven",
        "file_name": "stairway_to_heaven.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=QkF3oxziUI4",
        "video_id": "QkF3oxziUI4",
        "file_size": 9_200_000,
        "is_favorite": False,
        "artwork_url": None,
    },
    {
        "job_id": "test1-track-003",
        "user_email": "test1@example.com",
        "title": "Hotel California",
        "file_name": "hotel_california.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=lDK9QqIzhwk",
        "video_id": "lDK9QqIzhwk",
        "file_size": 7_800_000,
        "is_favorite": True,
        "artwork_url": None,
    },
    # test2 – 2 canciones
    {
        "job_id": "test2-track-001",
        "user_email": "test2@example.com",
        "title": "Bad Guy",
        "file_name": "bad_guy.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=DyDfgMOUjCI",
        "video_id": "DyDfgMOUjCI",
        "file_size": 4_100_000,
        "is_favorite": False,
        "artwork_url": "https://i.scdn.co/image/ab67616d0000b2732a038d3bf875d23e4aeaa84e",
    },
    {
        "job_id": "test2-track-002",
        "user_email": "test2@example.com",
        "title": "Blinding Lights",
        "file_name": "blinding_lights.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=4NRXx6U8ABQ",
        "video_id": "4NRXx6U8ABQ",
        "file_size": 5_300_000,
        "is_favorite": True,
        "artwork_url": None,
    },
]

PLAYLISTS = [
    {
        "name": "Rock Clásico",
        "user_email": "test1@example.com",
        "track_job_ids": ["test1-track-001", "test1-track-002"],
    },
    {
        "name": "Pop Hits",
        "user_email": "test2@example.com",
        "track_job_ids": ["test2-track-001", "test2-track-002"],
    },
]

FEATURES = [
    {"user_email": "test1@example.com", "lyrics_enabled": True,  "share_enabled": True},
    {"user_email": "test2@example.com", "lyrics_enabled": False, "share_enabled": True},
]


def _ensure_dummy_file(file_name: str) -> str:
    """Crea un fichero MP3 dummy de 1 KB en /tmp y devuelve su path."""
    path = f"/tmp/{file_name}"
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(b"\x00" * 1024)
    return path


def seed():
    app = create_app()
    with app.app_context():
        created = {"users": 0, "tracks": 0, "playlists": 0, "features": 0}

        # ── Usuarios ──────────────────────────────────────────────────────────
        for u_data in USERS:
            if not db.session.get(User, u_data["email"]):
                db.session.add(User(**u_data))
                created["users"] += 1
            else:
                print(f"  · Usuario ya existe: {u_data['email']}")

        db.session.flush()

        # ── Canciones ─────────────────────────────────────────────────────────
        for t_data in TRACKS:
            if not Download.query.filter_by(job_id=t_data["job_id"]).first():
                file_path = _ensure_dummy_file(t_data["file_name"])
                db.session.add(Download(
                    job_id=t_data["job_id"],
                    status="done",
                    youtube_url=t_data["youtube_url"],
                    file_path=file_path,
                    file_name=t_data["file_name"],
                    title=t_data["title"],
                    user_email=t_data["user_email"],
                    video_id=t_data["video_id"],
                    file_size=t_data["file_size"],
                    is_favorite=t_data["is_favorite"],
                    artwork_url=t_data["artwork_url"],
                ))
                created["tracks"] += 1
            else:
                print(f"  · Track ya existe: {t_data['job_id']}")

        db.session.flush()

        # ── Playlists ─────────────────────────────────────────────────────────
        for pl_data in PLAYLISTS:
            existing = Playlist.query.filter_by(
                name=pl_data["name"],
                user_email=pl_data["user_email"],
            ).first()
            if not existing:
                pl = Playlist(name=pl_data["name"], user_email=pl_data["user_email"])
                db.session.add(pl)
                db.session.flush()  # necesitamos pl.id
                for pos, job_id in enumerate(pl_data["track_job_ids"]):
                    db.session.add(PlaylistTrack(
                        playlist_id=pl.id,
                        job_id=job_id,
                        position=pos,
                    ))
                created["playlists"] += 1
            else:
                print(f"  · Playlist ya existe: '{pl_data['name']}' ({pl_data['user_email']})")

        # ── UserFeatures ──────────────────────────────────────────────────────
        for f_data in FEATURES:
            existing = UserFeature.query.filter_by(user_email=f_data["user_email"]).first()
            if not existing:
                db.session.add(UserFeature(
                    user_email=f_data["user_email"],
                    lyrics_enabled=f_data["lyrics_enabled"],
                    share_enabled=f_data["share_enabled"],
                ))
                created["features"] += 1
            else:
                print(f"  · UserFeature ya existe: {f_data['user_email']}")

        db.session.commit()

        print(f"\n✓ Seed completado:")
        print(f"  usuarios creados : {created['users']}")
        print(f"  canciones creadas: {created['tracks']}")
        print(f"  playlists creadas: {created['playlists']}")
        print(f"  features creadas : {created['features']}")


if __name__ == "__main__":
    seed()
