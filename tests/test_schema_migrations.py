"""Rebuilding downloads/playlists to attach the foreign keys ALTER TABLE could not.

A dry run against a copy of the production database caught two mistakes here
before they shipped, and both are pinned below: index DDL must be captured
before the old table is dropped, and losing ix_downloads_job_id breaks the
foreign keys that playlist_tracks and play_events have on downloads.job_id —
that unique index is where the column's uniqueness lives, since the table
definition has no UNIQUE constraint on it.
"""
import os
import re
import tempfile

import pytest
from sqlalchemy import text


def _fresh_db(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "m.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    return path


@pytest.fixture
def legacy(monkeypatch):
    """A database shaped like production: the column exists, the FK does not."""
    path = _fresh_db(monkeypatch)
    from app import create_app, db
    from app.models import Download, User
    from app.player_models import Playlist, PlaylistTrack

    app = create_app()
    with app.app_context():
        db.create_all()
        with db.engine.connect() as conn:
            # Strip the FK the models put there, leaving the shape ALTER TABLE
            # produced in production: column present, constraint absent.
            for table in ("downloads", "playlists"):
                sql = conn.execute(text(
                    "SELECT sql FROM sqlite_master WHERE name = :t"), {"t": table}
                ).scalar()
                # Remove the clause *and* its preceding comma, or the
                # statement is left with a dangling comma.
                stripped = re.sub(
                    r",\s*FOREIGN\s*KEY\s*\(\s*user_email\s*\)\s*"
                    r"REFERENCES\s+users\s*\(\s*email\s*\)",
                    "", sql, flags=re.IGNORECASE)
                assert "FOREIGN KEY" not in stripped.upper()
                idx = conn.execute(text(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:t "
                    "AND sql IS NOT NULL"), {"t": table}).fetchall()
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(text(re.sub(
                    rf'CREATE TABLE ("?){table}\1', f"CREATE TABLE {table}__t",
                    stripped, count=1)))
                conn.execute(text(f"DROP TABLE {table}"))
                conn.execute(text(f"ALTER TABLE {table}__t RENAME TO {table}"))
                for (isql,) in idx:
                    conn.execute(text(isql))
                conn.commit()

        db.session.add(User(email="u@x.z", name="U"))
        db.session.commit()
        db.session.add(Download(job_id="j1", youtube_url="https://youtu.be/a",
                                user_email="u@x.z", status="done"))
        pl = Playlist(name="P", user_email="u@x.z")
        db.session.add(pl)
        db.session.flush()
        db.session.add(PlaylistTrack(playlist_id=pl.id, job_id="j1", position=0))
        db.session.commit()
    return path


def _fks(conn, table):
    return {r[3] for r in conn.execute(text(f"PRAGMA foreign_key_list({table})")).fetchall()}


def _indexes(conn, table):
    return {r[0] for r in conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t "
        "AND sql IS NOT NULL"), {"t": table}).fetchall()}


def test_the_fixture_really_lacks_the_foreign_keys(legacy):
    """Guard the guard: if the fixture stopped reproducing production's shape,
    every test below would pass without exercising anything."""
    import sqlite3
    conn = sqlite3.connect(legacy)  # inspected directly; booting would migrate it
    try:
        for table in ("downloads", "playlists"):
            assert not any(
                row[3] == "user_email"
                for row in conn.execute(f"PRAGMA foreign_key_list({table})")
            ), f"{table} already has the FK — the fixture is not reproducing production"
    finally:
        conn.close()


def test_migration_adds_both_foreign_keys(legacy, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    from app import create_app, db

    app = create_app()  # migration runs on boot

    with app.app_context(), db.engine.connect() as conn:
        assert "user_email" in _fks(conn, "downloads")
        assert "user_email" in _fks(conn, "playlists")


def test_no_data_is_lost(legacy, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    from app import create_app, db
    from app.models import Download

    app = create_app()

    with app.app_context():
        row = Download.query.filter_by(job_id="j1").first()
        assert row is not None
        assert row.user_email == "u@x.z"


def test_indexes_survive_the_rebuild(legacy, monkeypatch):
    """Captured before the drop, or they come back empty."""
    import sqlite3
    before = {r[0] for r in sqlite3.connect(legacy).execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='downloads' "
        "AND sql IS NOT NULL")}
    assert before, "fixture should have indexes to lose"

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    from app import create_app, db
    app = create_app()

    with app.app_context(), db.engine.connect() as conn:
        assert _indexes(conn, "downloads") == before


def test_the_unique_index_that_two_foreign_keys_depend_on_survives(legacy, monkeypatch):
    """downloads.job_id has no UNIQUE constraint — its uniqueness is the index.
    playlist_tracks and play_events both reference it, and SQLite rejects those
    foreign keys outright if that index is gone."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    from app import create_app, db
    app = create_app()

    with app.app_context(), db.engine.connect() as conn:
        unique = [r[1] for r in conn.execute(
            text("PRAGMA index_list(downloads)")).fetchall() if r[2]]
        assert "ix_downloads_job_id" in unique
        # The proof: this raises "foreign key mismatch" if it did not survive.
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_a_backup_is_written(legacy, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    from app import create_app
    create_app()
    assert os.path.isfile(legacy + ".pre-fk-backup")


def test_migration_is_idempotent(legacy, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    from app import create_app, db
    from app.schema_migrations import ensure_user_foreign_keys

    create_app()
    app = create_app()

    with app.app_context():
        second = ensure_user_foreign_keys(db)
    assert second["rebuilt"] == []
    assert sorted(second["skipped"]) == ["downloads", "playlists"]
