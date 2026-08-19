"""Foreign keys are enforced, and deleting a user takes everything with it.

SQLite leaves foreign keys off by default, so the constraints declared in the
schema were never enforced and every delete path had to remember its own
cleanup by hand. Three of them did not.
"""
import pytest

from app import db
from app.models import Download, PlaylistBatch, User
from app.player_models import (
    ApiToken, PlayEvent, Playlist, PlaylistMember, PlaylistShare, PlaylistTrack,
    UserFeature,
)

REMOTE = {"CF-Connecting-IP": "8.8.8.8"}  # bypasses the local-admin shortcut


@pytest.fixture
def populated_user(app):
    """A user owning one of everything that references them."""
    with app.app_context():
        db.create_all()
        for model in (PlayEvent, PlaylistTrack, PlaylistShare, PlaylistMember,
                      ApiToken, UserFeature, PlaylistBatch, Playlist, Download, User):
            model.query.delete()
        db.session.commit()

        email = "victim@example.test"
        db.session.add(User(email=email, name="Victim", is_admin=False))
        db.session.add(User(email="admin@example.test", name="Admin", is_admin=True))
        db.session.commit()

        raw, token_hash, prefix = ApiToken.generate()
        db.session.add(ApiToken(
            user_email=email, name="cli", token_hash=token_hash, token_prefix=prefix,
        ))
        db.session.add(UserFeature(user_email=email, lyrics_enabled=True))
        db.session.add(PlaylistBatch(
            batch_id="BATCH1", user_email=email, track_count=1,
            youtube_url="https://youtube.com/playlist?list=Z",
        ))
        db.session.add(Download(
            job_id="j1", youtube_url="https://youtu.be/x", status="done",
            user_email=email, file_path=None,
        ))
        pl = Playlist(name="Suya", user_email=email)
        db.session.add(pl)
        db.session.flush()
        db.session.add(PlaylistTrack(playlist_id=pl.id, job_id="j1", position=0))
        db.session.add(PlaylistShare(playlist_id=pl.id, token="tok-1", mode="view"))
        db.session.add(PlaylistMember(playlist_id=pl.id, user_email=email, role="owner"))
        db.session.add(PlayEvent(user_email=email, job_id="j1", seconds_played=10))
        db.session.commit()
        return {"email": email, "raw_token": raw, "playlist_id": pl.id}


def test_foreign_keys_are_enforced(app):
    with app.app_context():
        enabled = db.session.execute(db.text("PRAGMA foreign_keys")).scalar()
    assert enabled == 1, "foreign key enforcement is off"


def test_deleting_a_user_succeeds_and_removes_everything(app, client, populated_user):
    email = populated_user["email"]

    resp = client.delete(f"/db/api/users/{email}")
    assert resp.status_code == 200, resp.get_data(as_text=True)

    with app.app_context():
        assert User.query.get(email) is None
        # Rows that belong to the user go away entirely.
        assert ApiToken.query.filter_by(user_email=email).count() == 0
        assert UserFeature.query.filter_by(user_email=email).count() == 0
        assert PlayEvent.query.filter_by(user_email=email).count() == 0
        assert PlaylistMember.query.filter_by(user_email=email).count() == 0
        assert Playlist.query.filter_by(user_email=email).count() == 0
        assert PlaylistTrack.query.filter_by(
            playlist_id=populated_user["playlist_id"]).count() == 0
        # Content is kept, just anonymised.
        assert Download.query.filter_by(job_id="j1").first().user_email is None
        assert PlaylistBatch.query.filter_by(batch_id="BATCH1").first().user_email is None


def test_no_orphans_survive_a_user_deletion(app, client, populated_user):
    client.delete(f"/db/api/users/{populated_user['email']}")

    with app.app_context():
        violations = db.session.execute(db.text("PRAGMA foreign_key_check")).fetchall()
    assert violations == [], f"deleting a user left dangling rows: {violations}"


def test_a_token_stops_working_once_its_user_is_deleted(app, client, populated_user):
    token = populated_user["raw_token"]
    auth = {**REMOTE, "Authorization": f"Bearer {token}"}

    # Sanity: the token works while the user exists.
    assert client.get("/player/api/tracks", headers=auth).status_code == 200

    client.delete(f"/db/api/users/{populated_user['email']}")

    resp = client.get("/player/api/tracks", headers=auth)
    assert resp.status_code != 200, \
        "a token belonging to a deleted user still authenticates"


def test_deleting_a_playlist_leaves_nothing_behind(app, client, populated_user):
    pid = populated_user["playlist_id"]

    resp = client.delete(f"/player/api/playlists/{pid}")
    assert resp.status_code == 200

    with app.app_context():
        assert PlaylistTrack.query.filter_by(playlist_id=pid).count() == 0
        assert PlaylistShare.query.filter_by(playlist_id=pid).count() == 0
        assert PlaylistMember.query.filter_by(playlist_id=pid).count() == 0
        violations = db.session.execute(db.text("PRAGMA foreign_key_check")).fetchall()
    assert violations == []


def test_deleting_a_download_leaves_nothing_behind(app, client, populated_user):
    resp = client.post("/db/delete", json={"job_ids": ["j1"]})
    assert resp.status_code == 200

    with app.app_context():
        violations = db.session.execute(db.text("PRAGMA foreign_key_check")).fetchall()
    assert violations == []
