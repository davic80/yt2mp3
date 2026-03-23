import os
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, abort, jsonify, render_template, request, send_file

from app import db
from app.auth_utils import _is_local_request, get_current_user_email, user_required
from app.models import Download
from app.player_models import Playlist, PlaylistShare, PlaylistTrack, UserFeature, PlayEvent

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

    # Remote users may only stream their own tracks OR tracks in any shared playlist
    email = get_current_user_email()
    if email and record.user_email != email:
        shared_accessible = (
            db.session.query(PlaylistShare)
            .join(Playlist, PlaylistShare.playlist_id == Playlist.id)
            .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
            .filter(PlaylistTrack.job_id == job_id)
            .first()
        )
        if not shared_accessible:
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


# ── Playlist sharing API ───────────────────────────────────────────────────────

@player_bp.route("/api/playlists/<int:pid>/share", methods=["POST"])
@user_required
def api_share_playlist(pid: int):
    """Create (or return existing) share token for a playlist."""
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if email and pl.user_email != email:
        abort(403)

    share = PlaylistShare.query.filter_by(playlist_id=pid).first()
    if not share:
        share = PlaylistShare(playlist_id=pid, token=str(uuid.uuid4()))
        db.session.add(share)
        db.session.commit()

    return jsonify({"token": share.token})


@player_bp.route("/api/playlists/<int:pid>/share", methods=["DELETE"])
@user_required
def api_revoke_share(pid: int):
    """Revoke (delete) the share token for a playlist."""
    pl = Playlist.query.get_or_404(pid)

    email = get_current_user_email()
    if email and pl.user_email != email:
        abort(403)

    share = PlaylistShare.query.filter_by(playlist_id=pid).first()
    if share:
        db.session.delete(share)
        db.session.commit()

    return jsonify({"ok": True})


@player_bp.route("/api/shared/<token>")
@user_required
def api_shared_playlist(token: str):
    """Return playlist name + tracks for a shared token (login required)."""
    share = PlaylistShare.query.filter_by(token=token).first()
    if not share:
        return jsonify({"name": None, "tracks": []})

    pl = share.playlist
    tracks = (
        PlaylistTrack.query
        .filter_by(playlist_id=pl.id)
        .order_by(PlaylistTrack.position)
        .all()
    )
    return jsonify({
        "name": pl.name,
        "tracks": [
            {
                "job_id":    t.job_id,
                "position":  t.position,
                "title":     t.download.title or t.job_id,
                "file_name": t.download.file_name,
                "file_size": t.download.file_size,
            }
            for t in tracks
        ],
    })


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


# ── User features API ─────────────────────────────────────────────────────────

@player_bp.route("/api/me/features")
@user_required
def api_me_features():
    """Return feature flags for the current user."""
    email = get_current_user_email()
    if not email:
        # Local/admin — all features on
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

    # Ownership / shared-access check (reuse stream logic)
    if email and record.user_email != email:
        shared_accessible = (
            db.session.query(PlaylistShare)
            .join(Playlist, PlaylistShare.playlist_id == Playlist.id)
            .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
            .filter(PlaylistTrack.job_id == job_id)
            .first()
        )
        if not shared_accessible:
            abort(403)

    # Check lyrics cache
    from app.player_models import LyricsCache
    cached = None
    if record.video_id:
        cached = LyricsCache.query.filter_by(video_id=record.video_id).first()
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
