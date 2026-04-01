import os
import re
import uuid
import zipfile
from io import BytesIO
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from flask import (
    Blueprint,
    Response,
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
    start_download, get_job, get_batch, extract_playlist,
    start_playlist_download, PLAYLIST_MAX_TRACKS,
)
from app.fingerprint import collect
from app.hardware_parser import detect_hardware, compute_identity_hash
from app.bot_score import compute_bot_score
from app.geo import geolocate
from app.auth_utils import user_required

bp = Blueprint("main", __name__)

YOUTUBE_RE = re.compile(
    r"^(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?|playlist\?|shorts/|embed/)|youtu\.be/)"
    r"[\w\-?=&%]+"
)


def _rate_limits():
    per_hour = current_app.config.get("RATE_LIMIT_PER_HOUR", "10")
    per_minute = current_app.config.get("RATE_LIMIT_PER_MINUTE", "3")
    return [f"{per_minute} per minute", f"{per_hour} per hour"]


def _strip_playlist_params(url: str):
    """Remove list/index/start_radio params, keeping only v= and t=.

    If the URL has a v= param, return a clean single-video URL.
    If it's a bare playlist URL (no v=), return it as-is — yt-dlp with
    noplaylist=True will download the first track of the list.
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        clean = {k: v[0] for k, v in params.items() if k in ("v", "t")}
        if not clean.get("v"):
            return url  # bare playlist — let yt-dlp grab the first track
        return urlunparse(parsed._replace(query=urlencode(clean)))
    except Exception:
        return url


def _is_bare_playlist(url: str) -> bool:
    """Return True if the URL is a playlist URL without a video ID (v=)."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        return "list" in params and "v" not in params
    except Exception:
        return False


def _extract_video_id(url: str) -> str | None:
    """Return the YouTube video ID from a URL, or None if not parseable.

    Handles:
      https://www.youtube.com/watch?v=XXXXXXXXXXX
      https://youtu.be/XXXXXXXXXXX
      https://www.youtube.com/shorts/XXXXXXXXXXX
      https://www.youtube.com/embed/XXXXXXXXXXX
    """
    try:
        parsed = urlparse(url)
        # youtu.be/<id>
        if parsed.netloc in ("youtu.be", "www.youtu.be"):
            vid = parsed.path.lstrip("/").split("/")[0].split("?")[0]
            return vid or None
        # youtube.com/watch?v=<id>
        params = parse_qs(parsed.query)
        if "v" in params:
            return params["v"][0] or None
        # youtube.com/shorts/<id>  or  /embed/<id>
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "embed"):
            return parts[1] or None
    except Exception:
        pass
    return None


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

    if not YOUTUBE_RE.match(youtube_url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    # ── v5.0.0: Playlist detection ───────────────────────────────────────────
    if _is_bare_playlist(youtube_url):
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
        db.session.add(batch)
        db.session.commit()

        # Store entries in session for the confirm step
        session[f"batch:{batch_id}:entries"] = entries

        return jsonify({
            "type": "playlist",
            "batch_id": batch_id,
            "title": pl_info["title"],
            "track_count": len(entries),
        }), 200

    # ── Single-video download (unchanged) ────────────────────────────────────
    # Strip playlist params — if URL has v=, use clean single-video URL
    clean_url = _strip_playlist_params(youtube_url)

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

    video_id = _extract_video_id(clean_url)

    record = Download(
        job_id="placeholder",
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
    db.session.flush()

    # v3.1.0 — remember the anonymous browser fingerprint so we can associate
    # these downloads with a user if they log in later in the same session.
    if not session.get("user_email") and identity:
        session["anon_identity_hash"] = identity

    job_id = start_download(app_obj, clean_url, download_dir, video_id=video_id)
    record.job_id = job_id
    db.session.commit()

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

    return send_from_directory(
        download_dir,
        f"{job_id}.mp3",
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

    entries = session.pop(f"batch:{batch_id}:entries", None)
    if not entries:
        return jsonify({"error": "Batch expired — please submit the URL again"}), 410

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

    # Build ZIP in memory
    buf = BytesIO()
    seen_names: dict[str, int] = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for dl in downloads:
            mp3_path = os.path.join(download_dir, f"{dl.job_id}.mp3")
            # If the file was deduped, use file_path from the record
            if not os.path.isfile(mp3_path) and dl.file_path and os.path.isfile(dl.file_path):
                mp3_path = dl.file_path
            if not os.path.isfile(mp3_path):
                continue

            name = dl.file_name or f"{dl.title or dl.job_id}.mp3"
            # Deduplicate filenames within the ZIP
            if name in seen_names:
                seen_names[name] += 1
                base, ext = os.path.splitext(name)
                name = f"{base} ({seen_names[name]}){ext}"
            else:
                seen_names[name] = 0

            zf.write(mp3_path, name)

    buf.seek(0)
    zip_name = f"{batch_rec.playlist_title or 'playlist'}.zip"

    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# ─── Error handlers ───────────────────────────────────────────────────────────

@bp.app_errorhandler(429)
def ratelimit_handler(e):
    return (
        jsonify({"error": "Too many requests. Please wait a moment before trying again."}),
        429,
    )
