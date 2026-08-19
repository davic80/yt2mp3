import json
import os
import uuid
from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_from_directory,
    abort,
    session,
)
from app import db, limiter
from app.models import Download, PlaylistBatch
from app.downloader import (
    start_download, new_job_id, get_job, get_batch, extract_playlist,
    start_playlist_download, PLAYLIST_MAX_TRACKS,
)
from app.fingerprint import collect
from app.hardware_parser import detect_hardware, compute_identity_hash
from app.bot_score import compute_bot_score
from app.geo import geolocate
from app.auth_utils import user_required
from app.youtube_url import (
    canonical_url, extract_video_id, is_bare_playlist, is_youtube_url,
)

bp = Blueprint("main", __name__)


def _rate_limits():
    per_hour = current_app.config.get("RATE_LIMIT_PER_HOUR", "10")
    per_minute = current_app.config.get("RATE_LIMIT_PER_MINUTE", "3")
    return [f"{per_minute} per minute", f"{per_hour} per hour"]


# ─── Pages ────────────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    if request.args.get("fragment"):
        return render_template("fragments/home.html")
    return render_template("shell.html", initial_fragment="home")


# ─── API ──────────────────────────────────────────────────────────────────────

@bp.route("/download", methods=["POST"])
@limiter.limit(lambda: "; ".join(_rate_limits()))
def download():
    data = request.get_json(silent=True) or {}
    youtube_url = (data.get("url") or "").strip()

    if not youtube_url:
        return jsonify({"error": "URL required"}), 400

    if not is_youtube_url(youtube_url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    # ── v5.0.0: Playlist detection ───────────────────────────────────────────
    if is_bare_playlist(youtube_url):
        # Require login for playlist downloads (auto-created playlist needs an owner)
        user_email = session.get("user_email")
        if not user_email:
            return jsonify({"error": "login_required", "type": "playlist"}), 401

        # Extract playlist metadata (no download yet)
        try:
            pl_info = extract_playlist(youtube_url)
        except Exception as exc:
            return jsonify({"error": f"Could not read playlist: {exc}"}), 400

        entries = pl_info.get("entries") or []
        if not entries:
            return jsonify({"error": "Playlist is empty or private"}), 400
        if len(entries) > PLAYLIST_MAX_TRACKS:
            return jsonify({
                "error": f"Playlist has {len(entries)} tracks (max {PLAYLIST_MAX_TRACKS})",
                "type": "playlist",
            }), 400

        # Collect visitor metadata
        meta = collect(client_fingerprint=data.get("fingerprint"))
        geo = geolocate(meta.get("ip_address"))

        # Create PlaylistBatch record
        batch_id = str(uuid.uuid4())
        batch = PlaylistBatch(
            batch_id=batch_id,
            youtube_url=youtube_url,
            playlist_title=pl_info["title"],
            track_count=len(entries),
            user_email=user_email,
            ip_address=meta.get("ip_address"),
            fingerprint_hash=meta.get("fingerprint_hash"),
            country_code=geo["country_code"],
            city=geo["city"],
        )
        batch.entries_json = json.dumps(entries)
        db.session.add(batch)
        db.session.commit()

        return jsonify({
            "type": "playlist",
            "batch_id": batch_id,
            "title": pl_info["title"],
            "track_count": len(entries),
        }), 200

    # ── Single-video download ────────────────────────────────────────────────
    # Normalize to https://www.youtube.com/watch?v=<id> — drops playlist params
    # and the share tracking noise youtu.be links carry (?si=, ?is=, ...).
    clean_url = canonical_url(youtube_url)

    meta = collect(client_fingerprint=data.get("fingerprint"))
    fp_components = meta.get("fingerprint_components")
    geo = geolocate(meta.get("ip_address"))
    hardware = detect_hardware(fp_components)
    identity = compute_identity_hash(fp_components)
    bot = compute_bot_score(
        ua_raw=meta.get("user_agent_raw"),
        ua_is_bot=meta.get("ua_is_bot", False),
        fingerprint_hash=meta.get("fingerprint_hash"),
        fingerprint_components=fp_components,
        referrer=meta.get("referrer"),
    )

    app_obj = current_app._get_current_object()
    download_dir = current_app.config["DOWNLOAD_DIR"]

    video_id = extract_video_id(clean_url)

    # Reserve the job id up front: the row must be committed and findable
    # before the worker thread starts, or a dedup hit (which resolves with no
    # download at all) races the commit and leaves the row stuck at "pending".
    job_id = new_job_id()

    record = Download(
        job_id=job_id,
        youtube_url=youtube_url,
        hardware_model=hardware,
        identity_hash=identity,
        bot_score=bot,
        country_code=geo["country_code"],
        city=geo["city"],
        user_email=session.get("user_email"),  # None = anonymous
        video_id=video_id,
        **meta,
    )
    db.session.add(record)
    db.session.commit()

    # v3.1.0 — remember the anonymous browser fingerprint so we can associate
    # these downloads with a user if they log in later in the same session.
    if not session.get("user_email") and identity:
        session["anon_identity_hash"] = identity

    start_download(app_obj, job_id, clean_url, download_dir, video_id=video_id)

    return jsonify({"type": "single", "job_ids": [job_id]}), 202


@bp.route("/status/<job_id>")
def status(job_id: str):
    job = get_job(job_id)
    if job is None:
        record = Download.query.filter_by(job_id=job_id).first()
        if not record:
            abort(404)
        return jsonify(record.to_dict())

    resp = {
        "job_id": job_id,
        "status": job["status"],
        "progress": job.get("progress", 0),
        "title": job.get("title"),
        "file_name": job.get("file_name"),
        "file_size": job.get("file_size"),
        "error_message": job.get("error"),
    }
    return jsonify(resp)


@bp.route("/files/<path:filename>")
def serve_file(filename: str):
    safe_name = os.path.basename(filename)
    download_dir = current_app.config["DOWNLOAD_DIR"]

    job_id = os.path.splitext(safe_name)[0]
    record = Download.query.filter_by(job_id=job_id).first_or_404()

    if record.status != "done" or not record.file_path:
        abort(404)

    display_name = record.file_name or f"{job_id}.mp3"

    # Deduplicated and claimed rows share another job's file, so <job_id>.mp3
    # does not exist for them — fall back to the path stored on the record.
    served_name = f"{job_id}.mp3"
    if not os.path.isfile(os.path.join(download_dir, served_name)):
        candidate = os.path.realpath(record.file_path)
        root = os.path.realpath(download_dir)
        # Keep the served file inside DOWNLOAD_DIR even though the path comes
        # from our own DB rather than from the request.
        try:
            inside = os.path.commonpath([candidate, root]) == root
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(candidate):
            abort(404)
        served_name = os.path.basename(candidate)

    return send_from_directory(
        download_dir,
        served_name,
        as_attachment=True,
        download_name=display_name,
    )


# ─── Playlist batch endpoints (v5.0.0) ────────────────────────────────────────

@bp.route("/download/playlist/<batch_id>/confirm", methods=["POST"])
@user_required
def playlist_confirm(batch_id: str):
    """Start downloading all tracks in a confirmed playlist batch."""
    batch_rec = PlaylistBatch.query.filter_by(batch_id=batch_id).first_or_404()
    if batch_rec.status != "pending":
        return jsonify({"error": "Batch already started"}), 409

    if not batch_rec.entries_json:
        return jsonify({"error": "Batch expired — please submit the URL again"}), 410

    try:
        entries = json.loads(batch_rec.entries_json)
    except Exception:
        return jsonify({"error": "Batch payload is invalid — please submit the URL again"}), 410
    # Clear entries from DB now that they've been consumed
    batch_rec.entries_json = None
    db.session.commit()

    app_obj = current_app._get_current_object()
    download_dir = current_app.config["DOWNLOAD_DIR"]

    start_playlist_download(
        app_obj, batch_id, entries, download_dir,
        user_email=session.get("user_email"),
        playlist_title=batch_rec.playlist_title or "YouTube Playlist",
    )

    return jsonify({"ok": True}), 202


@bp.route("/download/playlist/<batch_id>/status")
def playlist_status(batch_id: str):
    """Return current progress of a playlist batch download."""
    batch = get_batch(batch_id)

    if batch is not None:
        return jsonify({
            "batch_id": batch_id,
            "status": batch["status"],
            "title": batch.get("title"),
            "total": batch.get("total", 0),
            "completed": batch.get("completed", 0),
            "failed": batch.get("failed", 0),
            "skipped": batch.get("skipped", 0),
            "tracks": batch.get("tracks", []),
            "app_playlist_id": batch.get("app_playlist_id"),
        })

    # Fallback to DB (after container restart the in-memory store is empty)
    batch_rec = PlaylistBatch.query.filter_by(batch_id=batch_id).first()
    if not batch_rec:
        abort(404)

    # Reconstruct track list from Download rows
    tracks = []
    for dl in Download.query.filter_by(batch_id=batch_id).order_by(Download.id).all():
        tracks.append({
            "job_id": dl.job_id,
            "video_id": dl.video_id,
            "title": dl.title or dl.video_id or "?",
            "status": dl.status,
        })

    return jsonify({
        "batch_id": batch_id,
        "status": batch_rec.status,
        "title": batch_rec.playlist_title,
        "total": batch_rec.track_count,
        "completed": batch_rec.completed,
        "failed": batch_rec.failed,
        "skipped": batch_rec.skipped,
        "tracks": tracks,
        "app_playlist_id": batch_rec.app_playlist_id,
    })


@bp.route("/download/playlist/<batch_id>/zip")
@user_required
def playlist_zip(batch_id: str):
    """Stream a ZIP of all completed MP3s in a playlist batch."""
    batch_rec = PlaylistBatch.query.filter_by(batch_id=batch_id).first_or_404()
    if batch_rec.status not in ("done", "error"):
        return jsonify({"error": "Batch not finished yet"}), 409

    download_dir = current_app.config["DOWNLOAD_DIR"]

    downloads = (
        Download.query
        .filter_by(batch_id=batch_id, status="done")
        .order_by(Download.id)
        .all()
    )
    if not downloads:
        return jsonify({"error": "No completed tracks"}), 404

    # The archive streams from disk now, so this is no longer a memory limit —
    # it only exists so a playlist cannot be downloaded in a form larger than
    # the playlist itself is allowed to be.
    max_zip_tracks = current_app.config.get("PLAYLIST_ZIP_MAX_TRACKS", PLAYLIST_MAX_TRACKS)
    if len(downloads) > max_zip_tracks:
        return jsonify({
            "error": f"Playlist ZIP is limited to {max_zip_tracks} tracks",
            "completed_tracks": len(downloads),
            "max_tracks": max_zip_tracks,
        }), 413

    from app.zip_service import send_zip, unique_arcname

    seen: dict[str, int] = {}
    entries = []
    for dl in downloads:
        mp3_path = os.path.join(download_dir, f"{dl.job_id}.mp3")
        # Deduplicated rows share the original job's file.
        if not os.path.isfile(mp3_path) and dl.file_path and os.path.isfile(dl.file_path):
            mp3_path = dl.file_path
        entries.append((
            mp3_path,
            unique_arcname(dl.file_name or f"{dl.title or dl.job_id}.mp3", seen),
        ))

    zip_name = os.path.basename(f"{batch_rec.playlist_title or 'playlist'}.zip").strip()
    response = send_zip(entries, zip_name or "playlist.zip")
    if response is None:
        return jsonify({"error": "No completed tracks"}), 404
    return response


# ─── Error handlers ───────────────────────────────────────────────────────────

@bp.app_errorhandler(429)
def ratelimit_handler(e):
    return (
        jsonify({"error": "Too many requests. Please wait a moment before trying again."}),
        429,
    )
