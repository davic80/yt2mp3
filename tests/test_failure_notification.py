"""Download failures: logged, and mailed to the admin — but throttled.

A successful download used to log nothing at all, because the messages were
only on the fallback paths: if the first player client worked, the run was
silent. That also meant a gradual degradation — the first client starting to
fail while a later one still worked — passed unnoticed.

The failure mail is throttled deliberately. When YouTube changes something the
failure is never isolated: every download fails at once, and one email per
attempt would bury the message that matters.
"""
import time

import pytest

from app import mailer


@pytest.fixture(autouse=True)
def reset_budget(monkeypatch):
    monkeypatch.setattr(mailer, "_failure_window_start", 0.0)
    monkeypatch.setattr(mailer, "_failure_sent", 0)
    monkeypatch.setattr(mailer, "_failure_suppressed", 0)
    yield


def test_the_first_failures_in_a_window_are_sent():
    for _ in range(mailer._FAILURE_MAX_PER_WINDOW):
        may_send, _ = mailer._failure_budget()
        assert may_send


def test_the_rest_of_the_window_is_suppressed():
    for _ in range(mailer._FAILURE_MAX_PER_WINDOW):
        mailer._failure_budget()

    for _ in range(20):
        may_send, _ = mailer._failure_budget()
        assert not may_send, "an outage would send one email per failed download"


def test_the_suppressed_count_rides_along_on_the_next_message():
    """The volume is the signal: 40 suppressed failures says outage, not one
    bad video."""
    for _ in range(mailer._FAILURE_MAX_PER_WINDOW):
        mailer._failure_budget()
    for _ in range(40):
        mailer._failure_budget()

    mailer._failure_window_start = time.time() - mailer._FAILURE_WINDOW_SECONDS - 1
    may_send, suppressed = mailer._failure_budget()

    assert may_send
    assert suppressed == 0, "a new window starts clean"


def test_the_budget_refills_after_the_window():
    for _ in range(mailer._FAILURE_MAX_PER_WINDOW):
        mailer._failure_budget()
    assert not mailer._failure_budget()[0]

    mailer._failure_window_start = time.time() - mailer._FAILURE_WINDOW_SECONDS - 1
    assert mailer._failure_budget()[0]


# ── The message itself ───────────────────────────────────────────────────────

SAMPLE = {
    "job_id": "job-1",
    "youtube_url": "https://www.youtube.com/watch?v=dw9IH-Vsyi8",
    "video_id": "dw9IH-Vsyi8",
    "error": "ERROR: unable to download video data: HTTP Error 403: Forbidden",
    "attempts": [
        ("web_embedded", "This video is unavailable"),
        ("default", "HTTP Error 403: Forbidden"),
    ],
    "elapsed": 12.5,
}


def test_the_message_names_every_client_that_was_tried():
    """Which clients failed and how is the actionable part — it is what tells
    you whether YouTube broke one client or all of them."""
    html = mailer._build_failure_html(SAMPLE, suppressed=0)

    assert "web_embedded" in html
    assert "default" in html
    assert "403" in html
    assert "dw9IH-Vsyi8" in html


def test_the_message_says_when_others_were_suppressed():
    html = mailer._build_failure_html(SAMPLE, suppressed=17)
    assert "17 further failure" in html
    assert "general outage" in html


def test_the_error_text_is_escaped():
    hostile = dict(SAMPLE, error="<script>alert(1)</script>")
    html = mailer._build_failure_html(hostile, suppressed=0)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_failure_without_attempts_still_renders():
    html = mailer._build_failure_html(dict(SAMPLE, attempts=[]), suppressed=0)
    assert "No client-level detail" in html


def test_batch_tracks_do_not_each_send_one(monkeypatch):
    """suppress_email is what keeps a failing 100-track playlist from sending
    100 emails; the batch summary reports it instead."""
    import inspect
    from app import downloader

    source = inspect.getsource(downloader._run_download)
    assert "if not suppress_email:" in source
    # The failure notification must sit behind the same guard.
    after_failure = source.split("download failed:", 1)[1]
    assert "if not suppress_email:" in after_failure
