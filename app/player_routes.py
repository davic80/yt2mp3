import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import requests as _requests

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, send_file, url_for

from app import db
from app.auth_utils import _is_local_request, get_current_user_email, user_required
from app.models import Download
from app.player_models import Playlist, PlaylistShare, PlaylistMember, PlaylistTrack, UserFeature, PlayEvent

player_bp = Blueprint("player", __name__, url_prefix="/player")


# ── Collaborative playlist helpers ─────────────────────────────────────────────

def _can_edit(pl, email):
    """Return True if the user can add/remove/reorder tracks in this playlist."""
    if not email:
        return True  # local/admin bypass
    if pl.user_email == email:
        return True  # owner
    member = PlaylistMember.query.filter_by(
        playlist_id=pl.id, user_email=email
    ).first()
    return member is not None and member.role in ("owner", "editor")


def _is_owner(pl, email):
    """Return True if the user is the playlist owner (or local admin)."""
    if not email:
        return True  # local/admin bypass
    return pl.user_email == email


def _user_display_name(email):
    """Return user display name for an email, or the email itself as fallback."""
    if not email:
        return None
    from app.models import User
    u = User.query.get(email)
    return u.name if u and u.name else email


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

    # Remote users may only stream their own tracks, tracks in any shared playlist,
    # or tracks in a collaborative playlist they are a member of
    email = get_current_user_email()
    if email and record.user_email != email:
        shared_accessible = (
            db.session.query(PlaylistShare)
            .join(Playlist, PlaylistShare.playlist_id == Playlist.id)
            .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
            .filter(PlaylistTrack.job_id == job_id)
            .first()
        )
        member_accessible = (
            db.session.query(PlaylistMember)
            .join(Playlist, PlaylistMember.playlist_id == Playlist.id)
            .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
            .filter(PlaylistMember.user_email == email, PlaylistTrack.job_id == job_id)
            .first()
        )
        if not shared_accessible and not member_accessible:
            abort(403)

    return _stream_mp3(record)


# ── Tracks API ─────────────────────────────────────────────────────────────────

@player_bp.route("/api/tracks")
@user_required
def api_tracks():
    email = get_current_user_email()

    # Local admin can impersonate a user with ?as=email to see their tracks
    if not email and _is_local_request():
        email = request.args.get("as") or None

    query = Download.query.filter_by(status="done")

    # Local (admin) without ?as → email is None → sees all tracks
    # Local admin with ?as=email → sees that user's tracks
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
            "video_id":    r.video_id,
            "artwork_url": r.artwork_url,
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


# ── Artwork API ────────────────────────────────────────────────────────────────

def _parse_title_parts(full_title: str):
    """Split 'Artist - Song' into (artist, song). Returns ('', full_title) if no separator."""
    parts = (full_title or "").split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", (full_title or "").strip()


def _itunes_lookup(artist: str, title: str) -> str | None:
    term = f"{artist} {title}".strip()
    try:
        r = _requests.get(
            "https://itunes.apple.com/search",
            params={"term": term, "media": "music", "entity": "song", "limit": 1},
            timeout=4,
        )
        results = r.json().get("results", [])
        if results:
            url = results[0].get("artworkUrl100", "")
            return url.replace("100x100bb", "600x600bb") if url else None
    except Exception:
        pass
    return None


def _deezer_lookup(artist: str, title: str) -> str | None:
    term = f"{artist} {title}".strip()
    try:
        r = _requests.get(
            "https://api.deezer.com/search",
            params={"q": term, "type": "track", "limit": 1},
            timeout=4,
        )
        data = r.json().get("data", [])
        if data:
            return data[0].get("album", {}).get("cover_big") or None
    except Exception:
        pass
    return None


@player_bp.route("/api/artwork/<job_id>")
@user_required
def api_artwork(job_id: str):
    """Return cached artwork URL for a track, fetching from iTunes/Deezer if not yet cached."""
    record = Download.query.filter_by(job_id=job_id, status="done").first_or_404()

    # Ownership check for remote users
    email = get_current_user_email()
    if email and record.user_email != email:
        abort(403)

    # Already cached (and not blacklisted)
    if record.artwork_url and not record.artwork_blacklisted:
        return jsonify({"artwork_url": record.artwork_url})

    # Don't re-fetch if blacklisted — return YouTube thumb or nothing
    if record.artwork_blacklisted:
        fallback = (
            f"https://img.youtube.com/vi/{record.video_id}/maxresdefault.jpg"
            if record.video_id else None
        )
        return jsonify({"artwork_url": fallback})

    artist, title = _parse_title_parts(record.title or "")
    # Strip common noise from title before searching
    clean_title = re.sub(
        r'\s*[\(\[].*?(official|video|audio|lyric|hd|4k|mv).*?[\)\]]',
        '', record.title or '', flags=re.IGNORECASE
    ).strip()
    c_artist, c_title = _parse_title_parts(clean_title)

    url = (
        _itunes_lookup(c_artist, c_title)
        or _itunes_lookup(artist, title)
        or _deezer_lookup(c_artist, c_title)
        or _deezer_lookup(artist, title)
        or (f"https://img.youtube.com/vi/{record.video_id}/maxresdefault.jpg"
            if record.video_id else None)
    )

    if url:
        record.artwork_url = url
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return jsonify({"artwork_url": url})


@player_bp.route("/api/artwork/<job_id>", methods=["DELETE"])
@user_required
def api_artwork_delete(job_id: str):
    """Blacklist current artwork so next fetch tries again from external APIs."""
    # Admin-only: only local requests (email is None)
    if get_current_user_email() is not None:
        abort(403)

    record = Download.query.filter_by(job_id=job_id).first_or_404()
    record.artwork_url         = None
    record.artwork_blacklisted = True
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "db error"}), 500
    return jsonify({"ok": True})


@player_bp.route("/api/artwork/<job_id>", methods=["PATCH"])
@user_required
def api_artwork_patch(job_id: str):
    """Admin: set a custom artwork URL for a track, clearing any blacklist."""
    if get_current_user_email() is not None:
        abort(403)

    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    record = Download.query.filter_by(job_id=job_id).first_or_404()
    record.artwork_url         = url
    record.artwork_blacklisted = False
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "db error"}), 500
    return jsonify({"ok": True, "artwork_url": url})


# ── Playlists API ──────────────────────────────────────────────────────────────

@player_bp.route("/api/playlists")
@user_required
def api_playlists():
    from sqlalchemy import case, func, literal, or_
    email = get_current_user_email()

    # Local admin can impersonate a user with ?as=email to see their playlists
    if not email and _is_local_request():
        email = request.args.get("as") or None

    # ── Owned playlists ──
    owned_q = (
        db.session.query(
            Playlist,
            func.count(PlaylistTrack.id).label("track_count"),
            literal("owner").label("role"),
        )
        .outerjoin(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
        .group_by(Playlist.id)
    )
    if email:
        owned_q = owned_q.filter(Playlist.user_email == email)

    # ── Collaborative playlists (where user is an editor) ──
    collab_rows = []
    if email:
        collab_q = (
            db.session.query(
                Playlist,
                func.count(PlaylistTrack.id).label("track_count"),
                PlaylistMember.role.label("role"),
            )
            .join(PlaylistMember, PlaylistMember.playlist_id == Playlist.id)
            .outerjoin(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
            .filter(PlaylistMember.user_email == email, PlaylistMember.role == "editor")
            .group_by(Playlist.id)
        )
        collab_rows = collab_q.all()

    owned_rows = owned_q.order_by(
        case((Playlist.last_added.is_(None), 1), else_=0),
        Playlist.last_added.desc(),
        Playlist.created_at.desc(),
    ).all()

    # Merge, owned first, then collaborative (avoiding duplicates)
    seen_ids = set()
    result = []
    for pl, tc, role in list(owned_rows) + list(collab_rows):
        if pl.id in seen_ids:
            continue
        seen_ids.add(pl.id)
        member_count = PlaylistMember.query.filter_by(playlist_id=pl.id).count()
        is_collab = member_count > 1 or role == "editor"
        result.append({
            "id":               pl.id,
            "name":             pl.name,
            "track_count":      tc,
            "last_added":       pl.last_added.isoformat() if pl.last_added else None,
            "is_collaborative": is_collab,
            "role":             role if isinstance(role, str) else str(role),
            "member_count":     member_count,
        })

    return jsonify(result)


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
    db.session.flush()

    # Register creator as owner in playlist_members
    if email:
        db.session.add(PlaylistMember(playlist_id=pl.id, user_email=email, role="owner"))

    db.session.commit()
    return jsonify({"id": pl.id, "name": pl.name}), 201


@player_bp.route("/api/playlists/<int:pid>", methods=["DELETE"])
@user_required
def api_delete_playlist(pid: int):
    pl = Playlist.query.get_or_404(pid)

    # Owner-only operation
    email = get_current_user_email()
    if not _is_owner(pl, email):
        abort(403)

    # Delete members + shares before the playlist itself
    PlaylistMember.query.filter_by(playlist_id=pid).delete(synchronize_session=False)
    PlaylistShare.query.filter_by(playlist_id=pid).delete(synchronize_session=False)
    db.session.delete(pl)
    db.session.commit()
    return jsonify({"ok": True})


@player_bp.route("/api/playlists/<int:pid>/tracks")
@user_required
def api_playlist_tracks(pid: int):
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if not _can_edit(pl, email):
        abort(403)

    tracks = (
        PlaylistTrack.query
        .filter_by(playlist_id=pid)
        .order_by(PlaylistTrack.position)
        .all()
    )
    return jsonify([
        {
            "job_id":        t.job_id,
            "position":      t.position,
            "title":         t.download.title or t.job_id,
            "file_name":     t.download.file_name,
            "file_size":     t.download.file_size,
            "is_favorite":   bool(t.download.is_favorite),
            "added_by":      t.added_by,
            "added_by_name": _user_display_name(t.added_by) if t.added_by else None,
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
    if not _can_edit(pl, email):
        abort(403)

    Download.query.filter_by(job_id=job_id).first_or_404()

    # Duplicate guard — silently succeed if track is already in this playlist
    existing = PlaylistTrack.query.filter_by(playlist_id=pid, job_id=job_id).first()
    if existing:
        return jsonify({"ok": True, "already_exists": True, "position": existing.position})

    max_pos = db.session.query(
        db.func.max(PlaylistTrack.position)
    ).filter_by(playlist_id=pid).scalar() or -1

    pt = PlaylistTrack(playlist_id=pid, job_id=job_id, position=max_pos + 1, added_by=email)
    pl.last_added = datetime.now(timezone.utc)
    db.session.add(pt)
    db.session.commit()
    return jsonify({"ok": True, "position": pt.position}), 201


@player_bp.route("/api/playlists/<int:pid>/tracks/<job_id>", methods=["DELETE"])
@user_required
def api_remove_from_playlist(pid: int, job_id: str):
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if not _can_edit(pl, email):
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


@player_bp.route("/api/playlists/<int:pid>/reorder", methods=["PUT"])
@user_required
def api_reorder_playlist(pid: int):
    """Reorder tracks within a playlist. Expects JSON: { "order": ["job_id_1", "job_id_2", ...] }"""
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if not _can_edit(pl, email):
        abort(403)

    data  = request.get_json(silent=True) or {}
    order = data.get("order", [])
    if not order:
        return jsonify({"error": "order required"}), 400

    tracks = PlaylistTrack.query.filter_by(playlist_id=pid).all()
    by_job = {t.job_id: t for t in tracks}

    for i, job_id in enumerate(order):
        if job_id in by_job:
            by_job[job_id].position = i

    db.session.commit()
    return jsonify({"ok": True})


# ── Playlist sharing API ───────────────────────────────────────────────────────

@player_bp.route("/api/playlists/<int:pid>/share", methods=["POST"])
@user_required
def api_share_playlist(pid: int):
    """Create (or return existing) share token for a playlist. Accepts optional 'mode'."""
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if not _is_owner(pl, email):
        abort(403)

    data = request.get_json(silent=True) or {}
    mode = data.get("mode")  # None means "don't change"
    if mode and mode not in ("view", "collaborate"):
        mode = None

    share = PlaylistShare.query.filter_by(playlist_id=pid).first()
    if not share:
        share = PlaylistShare(playlist_id=pid, token=str(uuid.uuid4()), mode=mode or "view")
        db.session.add(share)
        db.session.commit()
    elif mode and share.mode != mode:
        share.mode = mode
        db.session.commit()

    return jsonify({"token": share.token, "mode": share.mode})


@player_bp.route("/api/playlists/<int:pid>/share", methods=["DELETE"])
@user_required
def api_revoke_share(pid: int):
    """Revoke (delete) the share token for a playlist. Also removes editor members."""
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if not _is_owner(pl, email):
        abort(403)

    share = PlaylistShare.query.filter_by(playlist_id=pid).first()
    if share:
        # If it was a collaborative share, remove all editors
        if share.mode == "collaborate":
            PlaylistMember.query.filter_by(playlist_id=pid, role="editor").delete(
                synchronize_session=False
            )
        db.session.delete(share)
        db.session.commit()

    return jsonify({"ok": True})


@player_bp.route("/api/shared/<token>")
def api_shared_playlist(token: str):
    """Return playlist name + tracks for a shared token (no login required — token is the auth)."""
    share = PlaylistShare.query.filter_by(token=token).first()
    if not share:
        return jsonify({"name": None, "tracks": [], "mode": "view"})

    pl = share.playlist
    tracks = (
        PlaylistTrack.query
        .filter_by(playlist_id=pl.id)
        .order_by(PlaylistTrack.position)
        .all()
    )

    # Check if requesting user is already a member (for collaborative playlists)
    is_member = False
    member_role = None
    try:
        email = get_current_user_email()
        if email:
            member = PlaylistMember.query.filter_by(
                playlist_id=pl.id, user_email=email
            ).first()
            if member:
                is_member = True
                member_role = member.role
    except Exception:
        pass

    return jsonify({
        "name":        pl.name,
        "mode":        share.mode or "view",
        "is_member":   is_member,
        "member_role": member_role,
        "playlist_id": pl.id,
        "tracks": [
            {
                "job_id":        t.job_id,
                "position":      t.position,
                "title":         t.download.title or t.job_id,
                "file_name":     t.download.file_name,
                "file_size":     t.download.file_size,
                "added_by":      t.added_by,
                "added_by_name": _user_display_name(t.added_by) if t.added_by else None,
            }
            for t in tracks
        ],
    })


@player_bp.route("/s/<token>")
def shared_redirect(token: str):
    """Canonical short URL for shared playlists: /player/s/<token> → /player?shared=<token>.
    Works whether the user is logged in or not; login redirect preserves the ?shared= param.
    Preserves fragment=1 query param so SPA fetch gets the fragment, not the full shell."""
    frag = "&fragment=1" if request.args.get("fragment") else ""
    return redirect(f"/player?shared={token}{frag}")


@player_bp.route("/api/shared/<token>/join", methods=["POST"])
@user_required
def api_join_shared(token: str):
    """Join a collaborative playlist as an editor. Token must have mode='collaborate'."""
    email = get_current_user_email()
    if not email:
        return jsonify({"ok": True, "role": "admin"})  # local admin — no membership needed

    share = PlaylistShare.query.filter_by(token=token).first()
    if not share:
        return jsonify({"error": "invalid token"}), 404

    if (share.mode or "view") != "collaborate":
        return jsonify({"error": "not a collaborative link"}), 400

    pl = share.playlist

    # Already the owner
    if pl.user_email == email:
        return jsonify({"ok": True, "role": "owner", "playlist_id": pl.id})

    # Already a member
    existing = PlaylistMember.query.filter_by(
        playlist_id=pl.id, user_email=email
    ).first()
    if existing:
        return jsonify({"ok": True, "role": existing.role, "playlist_id": pl.id})

    # Join as editor
    member = PlaylistMember(playlist_id=pl.id, user_email=email, role="editor")
    db.session.add(member)
    db.session.commit()
    return jsonify({"ok": True, "role": "editor", "playlist_id": pl.id}), 201


@player_bp.route("/api/shared/<token>/claim/<job_id>", methods=["POST"])
@user_required
def api_claim_track(token: str, job_id: str):
    """Copy a shared track's Download record to the requesting user."""
    email = get_current_user_email()
    if not email:
        abort(403)  # local/admin users don't need to claim

    share = PlaylistShare.query.filter_by(token=token).first()
    if not share:
        return jsonify({"error": "invalid token"}), 404

    # Verify the job_id is actually in this shared playlist
    pt = PlaylistTrack.query.filter_by(
        playlist_id=share.playlist_id, job_id=job_id
    ).first()
    if not pt:
        abort(404)

    original = Download.query.filter_by(job_id=job_id, status="done").first_or_404()

    # Already owns it
    existing = Download.query.filter_by(
        job_id=job_id, user_email=email
    ).first()
    if existing:
        return jsonify({"ok": True, "already_owned": True})

    # Check by video_id to avoid duplicating if they already have same video
    if original.video_id:
        dup = Download.query.filter_by(
            video_id=original.video_id, user_email=email, status="done"
        ).first()
        if dup:
            return jsonify({"ok": True, "already_owned": True})

    new_record = Download(
        job_id       = str(uuid.uuid4()),
        user_email   = email,
        title        = original.title,
        file_name    = original.file_name,
        file_path    = original.file_path,   # shared file, no re-download
        file_size    = original.file_size,
        youtube_url  = original.youtube_url,
        status       = "done",
        video_id     = original.video_id,
        audio_hash   = original.audio_hash,
        created_at   = datetime.now(timezone.utc),
        is_favorite  = False,
    )
    db.session.add(new_record)
    db.session.commit()
    return jsonify({"ok": True, "new_job_id": new_record.job_id})


@player_bp.route("/api/shared/<token>/add-playlist", methods=["POST"])
@user_required
def api_shared_add_playlist(token: str):
    """Bulk-claim all shared tracks and create a playlist containing them."""
    email = get_current_user_email()
    if not email:
        abort(403)

    share = PlaylistShare.query.filter_by(token=token).first()
    if not share:
        return jsonify({"error": "invalid token"}), 404

    src_pl = share.playlist
    src_tracks = (
        PlaylistTrack.query
        .filter_by(playlist_id=src_pl.id)
        .order_by(PlaylistTrack.position)
        .all()
    )

    # Claim each track (skip already-owned)
    claimed_job_ids = []
    for pt in src_tracks:
        original = Download.query.filter_by(job_id=pt.job_id, status="done").first()
        if not original:
            continue

        # Already owns by job_id
        existing = Download.query.filter_by(job_id=pt.job_id, user_email=email).first()
        if existing:
            claimed_job_ids.append(pt.job_id)
            continue

        # Already owns by video_id
        if original.video_id:
            dup = Download.query.filter_by(
                video_id=original.video_id, user_email=email, status="done"
            ).first()
            if dup:
                claimed_job_ids.append(dup.job_id)
                continue

        new_record = Download(
            job_id       = str(uuid.uuid4()),
            user_email   = email,
            title        = original.title,
            file_name    = original.file_name,
            file_path    = original.file_path,
            file_size    = original.file_size,
            youtube_url  = original.youtube_url,
            status       = "done",
            video_id     = original.video_id,
            audio_hash   = original.audio_hash,
            created_at   = datetime.now(timezone.utc),
            is_favorite  = False,
        )
        db.session.add(new_record)
        db.session.flush()
        claimed_job_ids.append(new_record.job_id)

    # Create a new playlist with the claimed tracks
    new_pl = Playlist(
        name       = src_pl.name,
        user_email = email,
        created_at = datetime.now(timezone.utc),
        last_added = datetime.now(timezone.utc),
    )
    db.session.add(new_pl)
    db.session.flush()

    for pos, jid in enumerate(claimed_job_ids):
        db.session.add(PlaylistTrack(playlist_id=new_pl.id, job_id=jid, position=pos))

    db.session.commit()
    return jsonify({
        "ok": True,
        "playlist_id": new_pl.id,
        "playlist_name": new_pl.name,
        "track_count": len(claimed_job_ids),
    }), 201


# ── User features API ─────────────────────────────────────────────────────────

@player_bp.route("/api/admin/users-list")
@user_required
def api_admin_users_list():
    """Return list of all users — local admin only, for the user picker in the player topbar."""
    if not _is_local_request():
        return jsonify({"error": "forbidden"}), 403
    from app.models import User
    users = User.query.order_by(User.email).all()
    return jsonify([
        {"email": u.email, "name": u.name or u.email}
        for u in users
    ])

@player_bp.route("/api/me/features")
@user_required
def api_me_features():
    """Return feature flags for the current user."""
    email = get_current_user_email()

    # Local admin can impersonate a user with ?as=email to see their features
    if not email and _is_local_request():
        as_email = request.args.get("as")
        if as_email:
            feat = UserFeature.query.filter_by(user_email=as_email).first()
            return jsonify({
                "lyrics_enabled": bool(feat.lyrics_enabled) if feat else False,
                "share_enabled":  bool(feat.share_enabled)  if feat else False,
            })
        # Local admin without ?as → all features on
        return jsonify({"lyrics_enabled": True, "share_enabled": True})

    feat = UserFeature.query.filter_by(user_email=email).first()
    return jsonify({
        "lyrics_enabled": bool(feat.lyrics_enabled) if feat else False,
        "share_enabled":  bool(feat.share_enabled)  if feat else False,
    })


# ── Play tracking API ─────────────────────────────────────────────────────────

@player_bp.route("/api/plays", methods=["POST"])
@user_required
def api_record_play():
    """Record a confirmed play event (>30 s listened or track ended)."""
    email = get_current_user_email()
    if not email:
        return jsonify({"ok": True})  # local/admin — no tracking needed

    data           = request.get_json(silent=True) or {}
    job_id         = data.get("job_id", "").strip()
    seconds_played = int(data.get("seconds_played", 0))

    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    # Dedup: ignore if same user played same track in last 60 s
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    recent = PlayEvent.query.filter(
        PlayEvent.user_email == email,
        PlayEvent.job_id     == job_id,
        PlayEvent.played_at  >= cutoff,
    ).first()
    if recent:
        return jsonify({"ok": True, "deduped": True})

    ev = PlayEvent(
        user_email     = email,
        job_id         = job_id,
        seconds_played = max(0, seconds_played),
        played_at      = datetime.now(timezone.utc),
    )
    db.session.add(ev)
    db.session.commit()
    return jsonify({"ok": True})


# ── Lyrics API ────────────────────────────────────────────────────────────────

@player_bp.route("/api/lyrics/<job_id>")
@user_required
def api_lyrics(job_id: str):
    """Fetch lyrics for a track. Checks cache first, then LRCLIB, then Lyrics.ovh."""
    import urllib.request
    import urllib.parse
    import json as _json

    # Check feature flag
    email = get_current_user_email()
    if email:
        feat = UserFeature.query.filter_by(user_email=email).first()
        if not feat or not feat.lyrics_enabled:
            return jsonify({"error": "lyrics not enabled"}), 403

    record = Download.query.filter_by(job_id=job_id, status="done").first_or_404()

    # Ownership / shared-access / member-access check (reuse stream logic)
    if email and record.user_email != email:
        shared_accessible = (
            db.session.query(PlaylistShare)
            .join(Playlist, PlaylistShare.playlist_id == Playlist.id)
            .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
            .filter(PlaylistTrack.job_id == job_id)
            .first()
        )
        member_accessible = (
            db.session.query(PlaylistMember)
            .join(Playlist, PlaylistMember.playlist_id == Playlist.id)
            .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
            .filter(PlaylistMember.user_email == email, PlaylistTrack.job_id == job_id)
            .first()
        )
        if not shared_accessible and not member_accessible:
            abort(403)

    # Check lyrics cache (skip if blacklisted for all sources)
    from app.player_models import LyricsCache, LyricsBlacklist
    cached = None
    blacklisted_all = False
    if record.video_id:
        bl = LyricsBlacklist.query.filter_by(video_id=record.video_id, source="*").first()
        blacklisted_all = bl is not None
        if not blacklisted_all:
            cached = LyricsCache.query.filter_by(video_id=record.video_id).first()
    if blacklisted_all:
        return jsonify({"error": "not_found"}), 404
    if cached:
        return jsonify({
            "source":      cached.source,
            "synced":      cached.synced,
            "content":     cached.content,
            "plain":       cached.plain,
        })

    title = record.title or ""
    # Strip common suffixes to improve match rate
    import re
    clean_title = re.sub(
        r'\s*[\(\[].*(official|video|audio|lyric|hd|4k|mv).*[\)\]]',
        '', title, flags=re.IGNORECASE
    ).strip()

    # -- Try LRCLIB (synced lyrics) --
    def _try_lrclib(q):
        url = "https://lrclib.net/api/search?q=" + urllib.parse.quote(q)
        req = urllib.request.Request(url, headers={"User-Agent": "yt2mp3/4.4.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                results = _json.loads(r.read())
            if results:
                best = results[0]
                return {
                    "synced":  bool(best.get("syncedLyrics")),
                    "content": best.get("syncedLyrics") or best.get("plainLyrics") or "",
                    "plain":   best.get("plainLyrics") or "",
                }
        except Exception:
            pass
        return None

    # -- Try Lyrics.ovh (plain) --
    def _try_ovh(title_str):
        # Lyrics.ovh needs artist + title; try splitting on " - "
        parts = title_str.split(" - ", 1)
        if len(parts) == 2:
            artist, song = parts[0].strip(), parts[1].strip()
        else:
            return None
        url = "https://api.lyrics.ovh/v1/{}/{}".format(
            urllib.parse.quote(artist), urllib.parse.quote(song)
        )
        req = urllib.request.Request(url, headers={"User-Agent": "yt2mp3/4.4.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                data = _json.loads(r.read())
            if data.get("lyrics"):
                return {"synced": False, "content": data["lyrics"], "plain": data["lyrics"]}
        except Exception:
            pass
        return None

    result = None
    source = "ovh"
    for _fn, _arg, _src in (
        (_try_lrclib, clean_title, "lrclib"),
        (_try_lrclib, title,       "lrclib"),
        (_try_ovh,    clean_title, "ovh"),
        (_try_ovh,    title,       "ovh"),
    ):
        result = _fn(_arg)
        if result:
            source = _src
            break
    content = result["content"] if result else ""
    plain   = result["plain"]   if result else ""
    synced  = result["synced"]  if result else False

    # Save to cache
    if record.video_id and (content or plain):
        cache_row = LyricsCache(
            video_id = record.video_id,
            source   = source,
            synced   = synced,
            content  = content,
            plain    = plain,
        )
        db.session.add(cache_row)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    if not content and not plain:
        return jsonify({"error": "not_found"}), 404

    return jsonify({"source": source, "synced": synced, "content": content, "plain": plain})


@player_bp.route("/api/lyrics/<job_id>/cache", methods=["DELETE"])
@user_required
def api_lyrics_cache_delete(job_id: str):
    """Admin: blacklist cached lyrics for a track so next request re-fetches from external APIs."""
    if get_current_user_email() is not None:
        abort(403)

    record = Download.query.filter_by(job_id=job_id).first_or_404()

    from app.player_models import LyricsCache, LyricsBlacklist

    # Delete the cached entry for this video_id
    if record.video_id:
        LyricsCache.query.filter_by(video_id=record.video_id).delete()
        # Add a per-source blacklist entry so we skip that source on next fetch
        # Using source="*" means: skip ALL sources (re-fetch will try all providers fresh)
        existing = LyricsBlacklist.query.filter_by(
            video_id=record.video_id, source="*"
        ).first()
        if not existing:
            db.session.add(LyricsBlacklist(video_id=record.video_id, source="*"))

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "db error"}), 500

    return jsonify({"ok": True})


@player_bp.route("/api/lyrics/<job_id>/cache", methods=["PATCH"])
@user_required
def api_lyrics_cache_patch(job_id: str):
    """Admin: set custom lyrics for a track, clearing any blacklist."""
    if get_current_user_email() is not None:
        abort(403)

    data   = request.get_json(silent=True) or {}
    lyrics = data.get("lyrics", "")
    if not isinstance(lyrics, str) or not lyrics.strip():
        return jsonify({"error": "lyrics required"}), 400

    record = Download.query.filter_by(job_id=job_id).first_or_404()

    from app.player_models import LyricsCache, LyricsBlacklist

    if record.video_id:
        # Remove any existing blacklist entry so the lyrics are served
        LyricsBlacklist.query.filter_by(video_id=record.video_id, source="*").delete()

        # Upsert LyricsCache with source='custom'
        existing = LyricsCache.query.filter_by(video_id=record.video_id).first()
        if existing:
            existing.source  = "custom"
            existing.synced  = False
            existing.content = lyrics
            existing.plain   = lyrics
        else:
            db.session.add(LyricsCache(
                video_id = record.video_id,
                source   = "custom",
                synced   = False,
                content  = lyrics,
                plain    = lyrics,
            ))

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "db error"}), 500
    return jsonify({"ok": True})
