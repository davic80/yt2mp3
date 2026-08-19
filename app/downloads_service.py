"""downloads_service.py — deleting downloads without leaving anything behind.

Two routes used to delete downloads, and each was incomplete in a different
way: ``/db/delete`` unlinked the file without checking whether another row
still pointed at it (so deleting a deduplicated row took the file out from
under other users), while ``/mis-descargas`` reference-counted the file but
left ``playlist_tracks`` and ``play_events`` rows dangling.

Dangling ``playlist_tracks`` are not cosmetic: SQLite does not enforce foreign
keys by default, so the row survives its ``Download`` and the player then
dereferences ``PlaylistTrack.download`` on a ``None``.

Both routes now share :func:`delete_downloads`, so the two halves cannot drift
apart again.
"""
import logging
import os

from app import db
from app.models import Download
from app.player_models import PlaylistTrack, PlayEvent

logger = logging.getLogger("app")


def _reindex_playlists(playlist_ids):
    """Close the gaps left in ``position`` after removing tracks."""
    for playlist_id in playlist_ids:
        tracks = (
            PlaylistTrack.query
            .filter_by(playlist_id=playlist_id)
            .order_by(PlaylistTrack.position)
            .all()
        )
        for index, track in enumerate(tracks):
            if track.position != index:
                track.position = index


def delete_downloads(job_ids, *, remove_files=True) -> dict:
    """Delete the given downloads and every row that references them.

    The audio file is unlinked only once no ``Download`` row points at it any
    more — deduplicated downloads and tracks claimed from a shared playlist
    share one file between several rows.

    Returns a summary dict; the caller commits nothing, this does.
    """
    job_ids = [j for j in (job_ids or []) if j]
    if not job_ids:
        return {"deleted": 0, "files_removed": 0, "playlist_tracks": 0, "play_events": 0}

    records = Download.query.filter(Download.job_id.in_(job_ids)).all()
    if not records:
        return {"deleted": 0, "files_removed": 0, "playlist_tracks": 0, "play_events": 0}

    found_ids = [r.job_id for r in records]
    file_paths = {r.file_path for r in records if r.file_path}

    # Playlists that will need their positions closing up afterwards.
    affected_playlists = {
        pt.playlist_id for pt in
        PlaylistTrack.query.filter(PlaylistTrack.job_id.in_(found_ids)).all()
    }

    removed_tracks = PlaylistTrack.query.filter(
        PlaylistTrack.job_id.in_(found_ids)
    ).delete(synchronize_session=False)

    removed_plays = PlayEvent.query.filter(
        PlayEvent.job_id.in_(found_ids)
    ).delete(synchronize_session=False)

    for record in records:
        db.session.delete(record)

    # Flush so the reference count below sees the rows as gone.
    db.session.flush()

    _reindex_playlists(affected_playlists)

    files_removed = 0
    if remove_files:
        for path in file_paths:
            still_used = Download.query.filter_by(file_path=path).count()
            if still_used:
                continue  # shared with a deduplicated or claimed row
            try:
                os.remove(path)
                files_removed += 1
            except OSError:
                pass  # already gone from disk — not an error

    db.session.commit()

    return {
        "deleted": len(found_ids),
        "files_removed": files_removed,
        "playlist_tracks": removed_tracks,
        "play_events": removed_plays,
    }


def purge_orphan_references() -> dict:
    """Delete rows left pointing at downloads that no longer exist.

    One-off cleanup for the orphans created before deletes went through
    :func:`delete_downloads`. Idempotent, so it is safe to run on every boot.
    """
    from sqlalchemy import text

    removed = {}
    for table in ("playlist_tracks", "play_events"):
        result = db.session.execute(text(
            f"DELETE FROM {table} WHERE job_id NOT IN (SELECT job_id FROM downloads)"
        ))
        removed[table] = result.rowcount or 0
    db.session.commit()

    if any(removed.values()):
        logger.info(
            "orphan cleanup: removed %d playlist_tracks, %d play_events",
            removed["playlist_tracks"], removed["play_events"],
        )
    return removed
