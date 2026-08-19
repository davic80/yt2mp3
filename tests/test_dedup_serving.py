"""Regression tests for deduplicated downloads.

Two bugs let a repeat download of an already-fetched video hand the browser a
207-byte Flask 404 page instead of an MP3:

  1. the worker thread started before the request handler committed the row,
     so a dedup hit (which resolves instantly, with no download) raced the
     commit and left the row stuck at "pending";
  2. /files/<job_id> always served "<job_id>.mp3", which does not exist for a
     deduped row — its file belongs to the original job.
"""
import os

import pytest

from app import db
from app.models import Download

VID = "dw9IH-Vsyi8"
URL = f"https://youtu.be/{VID}"


@pytest.fixture
def download_dir(app):
    return app.config["DOWNLOAD_DIR"]


@pytest.fixture
def original(app, download_dir):
    """An already-completed download whose MP3 exists on disk."""
    with app.app_context():
        db.create_all()
        job_id = "original-job"
        path = os.path.join(download_dir, f"{job_id}.mp3")
        with open(path, "wb") as fh:
            fh.write(b"ID3fake-mp3-bytes")
        db.session.add(Download(
            job_id=job_id,
            youtube_url=URL,
            video_id=VID,
            status="done",
            file_path=path,
            file_name="Cancion.mp3",
            file_size=os.path.getsize(path),
            audio_hash="a" * 64,
        ))
        db.session.commit()
    return "original-job"


def test_dedup_row_is_committed_before_the_worker_runs(app, client, original):
    """The row must be findable by job_id the moment the worker starts."""
    seen = {}

    def fake_start_download(_app, job_id, _url, _dir, video_id=None):
        # Stand in for the worker thread: look the row up exactly as
        # _run_download does, at the same point in time.
        with app.app_context():
            row = Download.query.filter_by(job_id=job_id).first()
            seen["found"] = row is not None
            seen["job_id_on_row"] = row.job_id if row else None
        return job_id

    import app.routes as routes
    original_fn = routes.start_download
    routes.start_download = fake_start_download
    try:
        resp = client.post("/download", json={"url": URL})
    finally:
        routes.start_download = original_fn

    assert resp.status_code == 202
    returned_job_id = resp.get_json()["job_ids"][0]

    assert seen["found"], "worker could not find the row — the race is back"
    assert seen["job_id_on_row"] == returned_job_id
    assert seen["job_id_on_row"] != "placeholder"


def test_files_endpoint_serves_the_deduped_file(app, client, original, download_dir):
    """A deduped row points at another job's file and must still download."""
    with app.app_context():
        db.session.add(Download(
            job_id="deduped-job",
            youtube_url=URL,
            video_id=VID,
            status="done",
            # The hallmark of a dedup: file_path belongs to the original job.
            file_path=os.path.join(download_dir, f"{original}.mp3"),
            file_name="Cancion.mp3",
            file_size=17,
            audio_hash="a" * 64,
        ))
        db.session.commit()

    assert not os.path.isfile(os.path.join(download_dir, "deduped-job.mp3"))

    resp = client.get("/files/deduped-job.mp3")

    assert resp.status_code == 200, "deduped download still 404s"
    assert resp.data == b"ID3fake-mp3-bytes"
    assert "audio" in resp.headers["Content-Type"] or resp.headers["Content-Type"].startswith("application/octet")
    assert "Cancion.mp3" in resp.headers["Content-Disposition"]


def test_files_endpoint_serves_a_normal_download(client, original):
    resp = client.get(f"/files/{original}.mp3")

    assert resp.status_code == 200
    assert resp.data == b"ID3fake-mp3-bytes"


def test_files_endpoint_404s_for_a_pending_row(app, client):
    with app.app_context():
        db.create_all()
        db.session.add(Download(
            job_id="pending-job", youtube_url=URL, video_id=VID, status="pending",
        ))
        db.session.commit()

    assert client.get("/files/pending-job.mp3").status_code == 404


def test_files_endpoint_404s_when_the_file_is_gone(app, client, download_dir):
    with app.app_context():
        db.create_all()
        db.session.add(Download(
            job_id="ghost-job",
            youtube_url=URL,
            video_id=VID,
            status="done",
            file_path=os.path.join(download_dir, "does-not-exist.mp3"),
            file_name="Fantasma.mp3",
        ))
        db.session.commit()

    assert client.get("/files/ghost-job.mp3").status_code == 404
