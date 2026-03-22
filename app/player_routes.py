import os
from datetime import datetime, timezone

from flask import Blueprint, Response, abort, jsonify, render_template, request, send_file

from app import db
from app.auth_utils import _is_local_request, get_current_user_email, user_required
from app.models import Download
from app.player_models import Playlist, PlaylistTrack

player_bp = Blueprint("player", __name__, url_prefix="/player")


# ── Page ───────────────────────────────────────────────────────────────────────

@player_bp.route("/")
@user_required
def index():
    if request.args.get("fragment"):
        return render_template("fragments/player.html")
    return render_template("shell.html", initial_fragment="player")


# ── Streaming ──────────────────────────────────────────────────────────────────

def _parse_range(range_header: str, size: int):
    """Parse 'bytes=START-END' → (start, end) clamped to file size."""
    _, _, range_spec = range_header.partition("=")
    start_str, _, end_str = range_spec.partition("-")
    start = int(start_str) if start_str else 0
    end   = int(end_str)   if end_str   else size - 1
    end   = min(end, size - 1)
    return start, end


def _stream_mp3(record: Download) -> Response:
    path = record.file_path
    size = os.path.getsize(path)
    range_header = request.headers.get("Range")

    if range_header:
        start, end = _parse_range(range_header, size)
        length = end - start + 1

        def _generator():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return Response(
            _generator(),
            status=206,
            headers={
                "Content-Range":  f"bytes {start}-{end}/{size}",
                "Accept-Ranges":  "bytes",
                "Content-Length": str(length),
                "Content-Type":   "audio/mpeg",
            },
        )

    return send_file(path, mimetype="audio/mpeg")


@player_bp.route("/stream/<job_id>")
@user_required
def stream(job_id: str):
    record = Download.query.filter_by(job_id=job_id, status="done").first_or_404()

    # Remote users may only stream their own tracks
    email = get_current_user_email()
    if email and record.user_email != email:
        abort(403)

    return _stream_mp3(record)


# ── Tracks API ─────────────────────────────────────────────────────────────────

@player_bp.route("/api/tracks")
@user_required
def api_tracks():
    email = get_current_user_email()
    query = Download.query.filter_by(status="done")

    # Local (admin) → email is None → sees all tracks
    # Remote logged-in user → filter to own tracks only
    if email:
        query = query.filter_by(user_email=email)

    rows = query.order_by(Download.created_at.desc()).all()
    return jsonify([
        {
            "job_id":      r.job_id,
            "title":       r.title or r.job_id,
            "file_name":   r.file_name,
            "file_size":   r.file_size,
            "created_at":  r.created_at.isoformat() if r.created_at else None,
            "is_favorite": bool(r.is_favorite),
        }
        for r in rows
    ])


@player_bp.route("/api/favorite", methods=["POST"])
@user_required
def api_favorite():
    data   = request.get_json(silent=True) or {}
    job_id = data.get("job_id", "").strip()
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    record = Download.query.filter_by(job_id=job_id).first_or_404()

    # Ownership check for remote users
    email = get_current_user_email()
    if email and record.user_email != email:
        abort(403)

    record.is_favorite = not bool(record.is_favorite)
    db.session.commit()
    return jsonify({"is_favorite": bool(record.is_favorite)})


# ── Playlists API ──────────────────────────────────────────────────────────────

@player_bp.route("/api/playlists")
@user_required
def api_playlists():
    from sqlalchemy import case, func
    email = get_current_user_email()

    query = (
        db.session.query(
            Playlist,
            func.count(PlaylistTrack.id).label("track_count"),
        )
        .outerjoin(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
        .group_by(Playlist.id)
    )

    # Remote users see only their own playlists; local sees all
    if email:
        query = query.filter(Playlist.user_email == email)

    rows = query.order_by(
        case((Playlist.last_added.is_(None), 1), else_=0),
        Playlist.last_added.desc(),
        Playlist.created_at.desc(),
    ).all()

    return jsonify([
        {
            "id":          pl.id,
            "name":        pl.name,
            "track_count": tc,
            "last_added":  pl.last_added.isoformat() if pl.last_added else None,
        }
        for pl, tc in rows
    ])


@player_bp.route("/api/playlists", methods=["POST"])
@user_required
def api_create_playlist():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    email = get_current_user_email()
    pl = Playlist(name=name, user_email=email)
    db.session.add(pl)
    db.session.commit()
    return jsonify({"id": pl.id, "name": pl.name}), 201


@player_bp.route("/api/playlists/<int:pid>", methods=["DELETE"])
@user_required
def api_delete_playlist(pid: int):
    pl = Playlist.query.get_or_404(pid)

    # Ownership check for remote users
    email = get_current_user_email()
    if email and pl.user_email != email:
        abort(403)

    db.session.delete(pl)
    db.session.commit()
    return jsonify({"ok": True})


@player_bp.route("/api/playlists/<int:pid>/tracks")
@user_required
def api_playlist_tracks(pid: int):
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if email and pl.user_email != email:
        abort(403)

    tracks = (
        PlaylistTrack.query
        .filter_by(playlist_id=pid)
        .order_by(PlaylistTrack.position)
        .all()
    )
    return jsonify([
        {
            "job_id":      t.job_id,
            "position":    t.position,
            "title":       t.download.title or t.job_id,
            "file_name":   t.download.file_name,
            "file_size":   t.download.file_size,
            "is_favorite": bool(t.download.is_favorite),
        }
        for t in tracks
    ])


@player_bp.route("/api/playlists/<int:pid>/tracks", methods=["POST"])
@user_required
def api_add_to_playlist(pid: int):
    pl     = Playlist.query.get_or_404(pid)
    data   = request.get_json(silent=True) or {}
    job_id = data.get("job_id", "").strip()
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    email = get_current_user_email()
    if email and pl.user_email != email:
        abort(403)

    Download.query.filter_by(job_id=job_id).first_or_404()

    max_pos = db.session.query(
        db.func.max(PlaylistTrack.position)
    ).filter_by(playlist_id=pid).scalar() or -1

    pt = PlaylistTrack(playlist_id=pid, job_id=job_id, position=max_pos + 1)
    pl.last_added = datetime.now(timezone.utc)
    db.session.add(pt)
    db.session.commit()
    return jsonify({"ok": True, "position": pt.position}), 201


@player_bp.route("/api/playlists/<int:pid>/tracks/<job_id>", methods=["DELETE"])
@user_required
def api_remove_from_playlist(pid: int, job_id: str):
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if email and pl.user_email != email:
        abort(403)

    pt = PlaylistTrack.query.filter_by(playlist_id=pid, job_id=job_id).first_or_404()
    removed_pos = pt.position
    db.session.delete(pt)
    db.session.flush()

    # Re-index positions for tracks after the removed one
    remaining = (
        PlaylistTrack.query
        .filter_by(playlist_id=pid)
        .filter(PlaylistTrack.position > removed_pos)
        .order_by(PlaylistTrack.position)
        .all()
    )
    for t in remaining:
        t.position -= 1

    db.session.commit()
    return jsonify({"ok": True})
