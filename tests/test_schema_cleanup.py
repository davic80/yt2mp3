"""The downloads table carried seven columns the app stopped writing.

They are leftovers from a tracking-pixel experiment (Meta _fbp/_fbc, Google
Analytics, Instagram) and an older playlist implementation. fingerprint.py has
stated "No cookie data is collected" for some time; the schema had not caught
up, and a rebuild generated from the model would have dropped them silently
along with anything they held.
"""
import os
import tempfile

import pytest
from sqlalchemy import text

DEAD = ["cookies_json", "fb_fbc", "fb_fbp",
        "ga_client", "ga_session", "ig_did", "playlist_url"]


def _columns(conn, table="downloads"):
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


@pytest.fixture
def legacy_db(monkeypatch):
    """A database shaped like production before the cleanup."""
    path = os.path.join(tempfile.mkdtemp(), "legacy.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    # Build the schema, then bolt the dead columns on the way production got
    # them: ALTER TABLE, one release at a time.
    from app import create_app, db
    app = create_app()
    with app.app_context():
        db.create_all()
        with db.engine.connect() as conn:
            for column in DEAD:
                conn.execute(text(f"ALTER TABLE downloads ADD COLUMN {column} TEXT"))
            conn.commit()
            assert DEAD[0] in _columns(conn)
    return path


def test_dead_columns_are_dropped(legacy_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy_db}")
    from app import create_app, db

    app = create_app()  # the migration runs here

    with app.app_context(), db.engine.connect() as conn:
        remaining = _columns(conn).intersection(DEAD)
    assert remaining == set(), f"still present: {sorted(remaining)}"


def test_a_column_holding_data_is_kept(legacy_db, monkeypatch):
    """Never drop on the assumption that a column is unused — check first."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy_db}")
    from app import create_app, db
    from app.models import Download

    app = create_app()
    with app.app_context():
        db.session.add(Download(job_id="keeper", youtube_url="https://youtu.be/x"))
        db.session.commit()
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE downloads ADD COLUMN ig_did TEXT"))
            conn.execute(text("UPDATE downloads SET ig_did = 'valioso' WHERE job_id = 'keeper'"))
            conn.commit()

    app2 = create_app()  # migration runs again

    with app2.app_context(), db.engine.connect() as conn:
        assert "ig_did" in _columns(conn), "dropped a column that still held data"
        kept = conn.execute(text("SELECT ig_did FROM downloads WHERE job_id = 'keeper'")).scalar()
    assert kept == "valioso"


def test_migration_is_idempotent(legacy_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy_db}")
    from app import create_app, db

    create_app()
    app = create_app()  # second boot must not raise

    with app.app_context(), db.engine.connect() as conn:
        assert _columns(conn).intersection(DEAD) == set()


@pytest.mark.parametrize("index", [
    "ix_downloads_user_email",
    "ix_downloads_video_id",
    "ix_downloads_audio_hash",
    "ix_downloads_batch_id",
])
def test_declared_indexes_exist(legacy_db, monkeypatch, index):
    """The models declare these; columns added by ALTER TABLE never got them,
    so the dedup lookup on video_id was scanning the table."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy_db}")
    from app import create_app, db

    app = create_app()

    with app.app_context(), db.engine.connect() as conn:
        names = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='downloads'"
        )).fetchall()}
    assert index in names
