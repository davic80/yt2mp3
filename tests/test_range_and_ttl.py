"""Range handling and in-memory store expiry.

A malformed Range header used to reach int() and come back as a 500, and
"bytes=999999-" past the end of the file produced a negative Content-Length.
The job stores, meanwhile, grew for the lifetime of the process.
"""
import time

import pytest

from app.downloader import (
    _JOB_TTL_SECONDS, _batches, _batches_lock, _jobs, _jobs_lock, sweep_stores,
)
from app.player_routes import _parse_range

SIZE = 1000


@pytest.mark.parametrize("header,expected", [
    ("bytes=0-99",    (0, 99)),
    ("bytes=100-",    (100, SIZE - 1)),
    ("bytes=-500",    (500, SIZE - 1)),      # suffix: the final 500 bytes
    ("bytes=0-99999", (0, SIZE - 1)),        # end clamped to the file
    ("bytes=999-999", (999, 999)),
])
def test_valid_ranges(header, expected):
    assert _parse_range(header, SIZE) == expected


@pytest.mark.parametrize("header", [
    "bytes=abc",          # used to raise ValueError -> 500
    "bytes=abc-def",
    "bytes=",
    "bytes=-",
    "bytes=-0",           # a zero-length suffix means nothing
    "bytes=500-100",      # inverted: end before start
    "bytes=1000-",        # starts exactly past the end
    "bytes=99999-",       # starts well past the end
    "bytes=-1-2",
    "items=0-99",         # wrong unit
    "0-99",               # no unit at all
    "bytes=0-99,200-299",  # multi-range: serving only part would corrupt it
])
def test_unsatisfiable_ranges_return_none(header):
    assert _parse_range(header, SIZE) is None


def test_negative_content_length_is_impossible():
    """The concrete symptom: start past EOF gave end - start + 1 < 0."""
    for header in ("bytes=1000-", "bytes=5000-6000"):
        assert _parse_range(header, SIZE) is None


# ── In-memory store expiry ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_stores():
    with _jobs_lock:
        _jobs.clear()
    with _batches_lock:
        _batches.clear()
    yield
    with _jobs_lock:
        _jobs.clear()
    with _batches_lock:
        _batches.clear()


def test_running_jobs_are_never_evicted():
    with _jobs_lock:
        _jobs["running"] = {"status": "pending", "progress": 40}
    sweep_stores()
    assert "running" in _jobs


def test_recently_finished_jobs_are_kept():
    with _jobs_lock:
        _jobs["fresh"] = {"status": "done", "finished_at": time.time()}
    sweep_stores()
    assert "fresh" in _jobs, "a just-finished job must still answer /status"


def test_old_finished_jobs_are_evicted():
    stale = time.time() - (_JOB_TTL_SECONDS + 60)
    with _jobs_lock:
        _jobs["old-done"] = {"status": "done", "finished_at": stale}
        _jobs["old-error"] = {"status": "error", "finished_at": stale}
    result = sweep_stores()

    assert result["jobs"] == 2
    assert _jobs == {}


def test_old_batches_are_evicted_too():
    stale = time.time() - (_JOB_TTL_SECONDS + 60)
    with _batches_lock:
        _batches["b"] = {"status": "done", "finished_at": stale}
    assert sweep_stores()["batches"] == 1


def test_creating_a_job_sweeps():
    from app.downloader import new_job_id

    with _jobs_lock:
        _jobs["ancient"] = {"status": "done",
                            "finished_at": time.time() - (_JOB_TTL_SECONDS + 60)}
    new_job_id()
    assert "ancient" not in _jobs, "the store never gets swept without a trigger"
