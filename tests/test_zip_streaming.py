"""ZIP downloads must not be assembled in memory.

The archives used to be built in a BytesIO, and the playlist one then called
getvalue() on top — a second full copy. Fifty tracks came to roughly half a
gigabyte of peak RSS on a Raspberry Pi, and that memory ceiling was also why
the playlist ZIP was capped at half the tracks a playlist may contain.
"""
import io
import os
import zipfile

import pytest

from app import db
from app.models import Download, PlaylistBatch
from app.zip_service import unique_arcname

TRACK_BYTES = b"ID3" + b"x" * 4093  # ~4 KB of incompressible-ish payload


@pytest.fixture
def batch(app):
    """A finished playlist batch with three completed tracks on disk."""
    download_dir = app.config["DOWNLOAD_DIR"]
    with app.app_context():
        db.create_all()
        Download.query.delete()
        PlaylistBatch.query.delete()
        db.session.commit()

        db.session.add(PlaylistBatch(
            batch_id="B1", youtube_url="https://youtube.com/playlist?list=X",
            playlist_title="Mi Lista", track_count=3, status="done",
        ))
        for i, jid in enumerate(("z1", "z2", "z3")):
            path = os.path.join(download_dir, f"{jid}.mp3")
            with open(path, "wb") as fh:
                fh.write(TRACK_BYTES)
            db.session.add(Download(
                job_id=jid, youtube_url="https://youtu.be/x", status="done",
                batch_id="B1", file_path=path, file_name=f"Cancion {i}.mp3",
                file_size=len(TRACK_BYTES),
            ))
        db.session.commit()
    return "B1"


def _open_zip(resp):
    return zipfile.ZipFile(io.BytesIO(resp.get_data()))


def test_playlist_zip_contains_every_track(client, batch):
    resp = client.get(f"/download/playlist/{batch}/zip")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    with _open_zip(resp) as zf:
        assert sorted(zf.namelist()) == ["Cancion 0.mp3", "Cancion 1.mp3", "Cancion 2.mp3"]
        assert zf.read("Cancion 0.mp3") == TRACK_BYTES


def test_playlist_zip_uses_the_deduped_file(app, client, batch):
    """A deduplicated row owns no file of its own; the ZIP must follow
    file_path rather than assuming <job_id>.mp3 exists."""
    download_dir = app.config["DOWNLOAD_DIR"]
    with app.app_context():
        db.session.add(Download(
            job_id="dup", youtube_url="https://youtu.be/x", status="done",
            batch_id=batch, file_path=os.path.join(download_dir, "z1.mp3"),
            file_name="Duplicada.mp3", file_size=len(TRACK_BYTES),
        ))
        db.session.commit()

    resp = client.get(f"/download/playlist/{batch}/zip")

    assert resp.status_code == 200
    with _open_zip(resp) as zf:
        assert "Duplicada.mp3" in zf.namelist()
        assert zf.read("Duplicada.mp3") == TRACK_BYTES


def test_zip_limit_matches_the_playlist_limit(app):
    """A playlist you are allowed to download must be one you can also zip."""
    from app.downloader import PLAYLIST_MAX_TRACKS

    with app.app_context():
        assert app.config["PLAYLIST_ZIP_MAX_TRACKS"] >= PLAYLIST_MAX_TRACKS, (
            "a full playlist can be downloaded but not zipped — the old 50 vs "
            "100 mismatch returned 413 with no way out"
        )


def test_mis_descargas_zip(client, batch):
    resp = client.get("/mis-descargas/api/tracks/zip")

    assert resp.status_code == 200
    with _open_zip(resp) as zf:
        assert len(zf.namelist()) == 3


def test_admin_zip(client, batch):
    resp = client.post("/db/download-zip", json={"job_ids": ["z1", "z2"]})

    assert resp.status_code == 200
    with _open_zip(resp) as zf:
        assert len(zf.namelist()) == 2


def test_zip_skips_files_missing_from_disk(app, client, batch):
    download_dir = app.config["DOWNLOAD_DIR"]
    os.remove(os.path.join(download_dir, "z2.mp3"))

    resp = client.get(f"/download/playlist/{batch}/zip")

    assert resp.status_code == 200
    with _open_zip(resp) as zf:
        assert "Cancion 1.mp3" not in zf.namelist()
        assert len(zf.namelist()) == 2


def test_zip_404s_when_no_file_survives(app, client, batch):
    download_dir = app.config["DOWNLOAD_DIR"]
    for jid in ("z1", "z2", "z3"):
        os.remove(os.path.join(download_dir, f"{jid}.mp3"))

    resp = client.get(f"/download/playlist/{batch}/zip")
    assert resp.status_code == 404


# ── Duplicate-name handling, previously duplicated in three places ───────────

def test_unique_arcname_disambiguates():
    seen = {}
    assert unique_arcname("a.mp3", seen) == "a.mp3"
    assert unique_arcname("a.mp3", seen) == "a (1).mp3"
    assert unique_arcname("a.mp3", seen) == "a (2).mp3"


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd", "passwd.mp3"),
    ("dir\\sub\\song.mp3", "song.mp3"),
    ("", "track.mp3"),
    (None, "track.mp3"),
    ("sin-extension", "sin-extension.mp3"),
])
def test_unique_arcname_is_safe(raw, expected):
    assert unique_arcname(raw, {}) == expected
