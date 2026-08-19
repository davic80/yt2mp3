"""Deleting a download must not leave rows pointing at it, and the player must
survive one that slipped through.

SQLite does not enforce foreign keys by default, so a playlist_tracks row can
outlive the Download it references. The player then dereferenced
PlaylistTrack.download on a None and returned 500 for the whole playlist.
"""
import contextlib
import os

import pytest

from app import db
from app.downloads_service import delete_downloads, purge_orphan_references
from app.models import Download, User
from app.player_models import Playlist, PlaylistTrack, PlayEvent

VID = "dw9IH-Vsyi8"


def _make_download(job_id, download_dir, *, file_path=None, video_id=VID):
    path = file_path if file_path is not None else os.path.join(download_dir, f"{job_id}.mp3")
    if file_path is None:
        with open(path, "wb") as fh:
            fh.write(b"ID3audio")
    return Download(
        job_id=job_id,
        youtube_url=f"https://youtu.be/{video_id}",
        video_id=video_id,
        status="done",
        file_path=path,
        file_name=f"{job_id}.mp3",
        file_size=8,
    )


@pytest.fixture
def seeded(app):
    """A playlist with three tracks, plus play events on the middle one."""
    download_dir = app.config["DOWNLOAD_DIR"]
    with app.app_context():
        db.create_all()
        for table in (PlayEvent, PlaylistTrack, Playlist, Download):
            table.query.delete()
        db.session.commit()

        # A play event references a real user — fabricating one for an email
        # that does not exist is invalid data, not a scenario worth testing.
        if User.query.get("x@y.z") is None:
            db.session.add(User(email="x@y.z", name="Oyente"))
            db.session.commit()

        for jid in ("a", "b", "c"):
            db.session.add(_make_download(jid, download_dir))
        pl = Playlist(name="Lista", user_email=None)
        db.session.add(pl)
        db.session.flush()
        for pos, jid in enumerate(("a", "b", "c")):
            db.session.add(PlaylistTrack(playlist_id=pl.id, job_id=jid, position=pos))
        db.session.add(PlayEvent(user_email="x@y.z", job_id="b", seconds_played=42))
        db.session.commit()
        return pl.id



@contextlib.contextmanager
def foreign_keys_off():
    """Build a scenario the foreign key constraint now prevents.

    Orphans can no longer be created through the app — that is the point of
    enabling the constraint. But databases that predate it may still hold
    them, so the guards that tolerate one still need testing.
    """
    raw = db.engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        yield cur
        raw.commit()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    finally:
        raw.close()


def test_delete_removes_playlist_tracks_and_play_events(app, seeded):
    with app.app_context():
        delete_downloads(["b"])

        assert Download.query.filter_by(job_id="b").first() is None
        assert PlaylistTrack.query.filter_by(job_id="b").first() is None
        assert PlayEvent.query.filter_by(job_id="b").first() is None


def test_delete_closes_the_gap_in_positions(app, seeded):
    with app.app_context():
        delete_downloads(["b"])

        remaining = (
            PlaylistTrack.query.filter_by(playlist_id=seeded)
            .order_by(PlaylistTrack.position).all()
        )
        assert [(t.job_id, t.position) for t in remaining] == [("a", 0), ("c", 1)]


def test_shared_file_survives_while_another_row_uses_it(app, seeded):
    """A deduplicated row shares the original's file — deleting it must not
    take the file away from the row that still points there."""
    download_dir = app.config["DOWNLOAD_DIR"]
    shared = os.path.join(download_dir, "a.mp3")
    with app.app_context():
        db.session.add(_make_download("dup", download_dir, file_path=shared))
        db.session.commit()

        delete_downloads(["dup"])
        assert os.path.isfile(shared), "file removed while 'a' still references it"

        delete_downloads(["a"])
        assert not os.path.isfile(shared), "file left behind once nothing referenced it"


def test_missing_file_on_disk_is_not_an_error(app, seeded):
    download_dir = app.config["DOWNLOAD_DIR"]
    with app.app_context():
        os.remove(os.path.join(download_dir, "c.mp3"))
        result = delete_downloads(["c"])
        assert result["deleted"] == 1


def test_playlist_endpoint_skips_a_track_whose_download_vanished(app, client, seeded):
    """The regression: an orphan must be skipped, not crash the playlist."""
    with app.app_context():
        # Legacy data: the row was deleted before the constraint existed.
        with foreign_keys_off() as cur:
            cur.execute("DELETE FROM downloads WHERE job_id = 'b'")
        assert PlaylistTrack.query.filter_by(job_id="b").first() is not None

    resp = client.get(f"/player/api/playlists/{seeded}/tracks")

    assert resp.status_code == 200, "orphaned track still crashes the playlist"
    assert [t["job_id"] for t in resp.get_json()] == ["a", "c"]


def test_purge_orphan_references_cleans_existing_rows(app, seeded):
    with app.app_context():
        with foreign_keys_off() as cur:
            cur.execute("DELETE FROM downloads WHERE job_id = 'b'")

        removed = purge_orphan_references()

        assert removed["playlist_tracks"] == 1
        assert removed["play_events"] == 1
        assert PlaylistTrack.query.filter_by(job_id="b").first() is None


def test_purge_is_idempotent(app, seeded):
    with app.app_context():
        assert purge_orphan_references() == {"playlist_tracks": 0, "play_events": 0}


# ── Route-level: both delete paths must behave identically ───────────────────

def test_admin_delete_route_leaves_no_orphans(app, client, seeded):
    resp = client.post("/db/delete", json={"job_ids": ["b"]})

    assert resp.status_code == 200
    with app.app_context():
        assert PlaylistTrack.query.filter_by(job_id="b").first() is None, \
            "/db/delete left a playlist_tracks orphan"
        assert PlayEvent.query.filter_by(job_id="b").first() is None, \
            "/db/delete left a play_events orphan"


def test_admin_delete_route_keeps_a_shared_file(app, client, seeded):
    """The dedup case: /db/delete used to unlink the file unconditionally,
    taking it away from every other row pointing at it."""
    download_dir = app.config["DOWNLOAD_DIR"]
    shared = os.path.join(download_dir, "a.mp3")
    with app.app_context():
        db.session.add(_make_download("dup", download_dir, file_path=shared))
        db.session.commit()

    client.post("/db/delete", json={"job_ids": ["dup"]})

    assert os.path.isfile(shared), \
        "/db/delete removed a file still referenced by another download"


def test_mis_descargas_delete_route_leaves_no_orphans(app, client, seeded):
    resp = client.delete("/mis-descargas/api/tracks/b")

    assert resp.status_code == 200
    with app.app_context():
        assert PlaylistTrack.query.filter_by(job_id="b").first() is None, \
            "/mis-descargas left a playlist_tracks orphan"
        assert PlayEvent.query.filter_by(job_id="b").first() is None, \
            "/mis-descargas left a play_events orphan"


def test_deleting_a_user_takes_their_playlist_tracks_with_it(app, client, seeded):
    """Playlist.tracks has delete-orphan cascade, but the user-deletion path
    uses a bulk query.delete() which bypasses the ORM entirely."""
    from app.models import User

    with app.app_context():
        db.session.add(User(email="owner@x.z", name="Owner"))
        pl = Playlist.query.get(seeded)
        pl.user_email = "owner@x.z"
        db.session.commit()

    resp = client.delete("/db/api/users/owner@x.z")
    assert resp.status_code == 200, resp.get_data(as_text=True)

    with app.app_context():
        assert Playlist.query.get(seeded) is None
        assert PlaylistTrack.query.filter_by(playlist_id=seeded).count() == 0, \
            "deleting the user left its playlist's tracks behind"
