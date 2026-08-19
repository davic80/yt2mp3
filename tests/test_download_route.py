"""Integration tests for POST /download — routing only, no real yt-dlp calls."""
import pytest

from app import db

VID = "dw9IH-Vsyi8"


@pytest.fixture
def started(app, monkeypatch):
    """Capture the URL handed to the downloader instead of downloading."""
    calls = []

    def fake_start_download(_app, url, _dir, video_id=None):
        calls.append({"url": url, "video_id": video_id})
        return "fake-job-id"

    monkeypatch.setattr("app.routes.start_download", fake_start_download)
    with app.app_context():
        db.create_all()
    return calls


@pytest.mark.parametrize("url", [
    # The exact link from the bug report, plus the other short-link shapes.
    f"https://youtu.be/{VID}?is=OyAAwX8ftDrncMII",
    f"https://youtu.be/{VID}?si=OyAAwX8ftDrncMII",
    f"https://youtu.be/{VID}",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://m.youtube.com/watch?v={VID}",
])
def test_short_links_download_as_single_track(client, started, url):
    resp = client.post("/download", json={"url": url})

    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert resp.get_json()["type"] == "single"

    # yt-dlp receives the canonical URL, with tracking params stripped.
    assert started[0]["url"] == f"https://www.youtube.com/watch?v={VID}"
    assert started[0]["video_id"] == VID


def test_video_inside_a_playlist_is_not_treated_as_a_batch(client, started):
    resp = client.post("/download", json={
        "url": f"https://youtu.be/{VID}?list=PLabcdef123",
    })

    assert resp.status_code == 202
    assert resp.get_json()["type"] == "single"
    assert started[0]["url"] == f"https://www.youtube.com/watch?v={VID}"


def test_bare_playlist_still_requires_login(client, started):
    resp = client.post("/download", json={
        "url": "https://www.youtube.com/playlist?list=PLabcdef123",
    })

    # Anonymous users can't start a playlist batch — the guard must still fire,
    # which proves bare playlists are routed to the batch branch.
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "login_required"
    assert started == []


def test_non_youtube_url_is_rejected(client, started):
    resp = client.post("/download", json={"url": "https://vimeo.com/12345"})

    assert resp.status_code == 400
    assert started == []
