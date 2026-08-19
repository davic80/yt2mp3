"""Unit tests for app.youtube_url — URL parsing has no Flask/network deps."""
import pytest

from app.youtube_url import (
    canonical_url,
    extract_video_id,
    is_bare_playlist,
    is_youtube_url,
)

VID = "dw9IH-Vsyi8"


# ── Accepted URL forms ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    # The share link that triggered the original bug report.
    f"https://youtu.be/{VID}?is=OyAAwX8ftDrncMII",
    f"https://youtu.be/{VID}",
    f"http://youtu.be/{VID}",
    f"youtu.be/{VID}",
    f"https://youtu.be/{VID}?si=abc123DEF",
    f"https://www.youtube.com/watch?v={VID}",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://music.youtube.com/watch?v={VID}",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://www.youtube.com/live/{VID}",
    "https://www.youtube.com/playlist?list=PLabcdef123",
])
def test_accepted_urls(url):
    assert is_youtube_url(url), f"should accept {url}"


@pytest.mark.parametrize("url", [
    "",
    "not a url",
    "https://vimeo.com/12345",
    "https://example.com/watch?v=abc",
    "https://notyoutube.com/watch?v=abc",
    "ftp://youtu.be/abc",
])
def test_rejected_urls(url):
    assert not is_youtube_url(url), f"should reject {url}"


# ── Video ID extraction ───────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    f"https://youtu.be/{VID}?is=OyAAwX8ftDrncMII",
    f"https://youtu.be/{VID}?si=xyz&t=42",
    f"youtu.be/{VID}",
    f"https://www.youtube.com/watch?v={VID}",
    f"https://www.youtube.com/watch?v={VID}&list=PLabc&index=3",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://www.youtube.com/live/{VID}",
])
def test_extract_video_id(url):
    assert extract_video_id(url) == VID


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/playlist?list=PLabcdef123",
    "https://vimeo.com/12345",
    "",
])
def test_extract_video_id_returns_none(url):
    assert extract_video_id(url) is None


# ── Canonicalization ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    f"https://youtu.be/{VID}?is=OyAAwX8ftDrncMII",
    f"https://youtu.be/{VID}?si=xyz",
    f"https://youtu.be/{VID}",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://www.youtube.com/watch?v={VID}&list=PLabc&index=3&start_radio=1",
])
def test_canonical_url_strips_noise(url):
    assert canonical_url(url) == f"https://www.youtube.com/watch?v={VID}"


def test_canonical_url_keeps_start_offset():
    assert canonical_url(f"https://youtu.be/{VID}?t=90&si=xyz") == (
        f"https://www.youtube.com/watch?v={VID}&t=90"
    )


def test_canonical_url_leaves_bare_playlist_untouched():
    url = "https://www.youtube.com/playlist?list=PLabcdef123"
    assert canonical_url(url) == url


# ── Playlist detection ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://www.youtube.com/playlist?list=PLabcdef123",
    "https://m.youtube.com/playlist?list=PLabcdef123",
])
def test_is_bare_playlist(url):
    assert is_bare_playlist(url)


@pytest.mark.parametrize("url", [
    # A video that merely happens to sit inside a playlist is NOT a bare
    # playlist — it must download as a single track.
    f"https://www.youtube.com/watch?v={VID}&list=PLabcdef123",
    f"https://youtu.be/{VID}?list=PLabcdef123",
    f"https://youtu.be/{VID}?is=OyAAwX8ftDrncMII",
    f"https://www.youtube.com/watch?v={VID}",
])
def test_not_bare_playlist(url):
    assert not is_bare_playlist(url)
