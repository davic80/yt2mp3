import os
import re
from urllib.parse import urlparse, parse_qs
from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_from_directory,
    abort,
)
from app import db, limiter
from app.models import Download
from app.downloader import start_download, get_job, extract_playlist_entries
from app.fingerprint import collect
from app.hardware_parser import detect_hardware, compute_identity_hash
from app.bot_score import compute_bot_score
from app.geo import geolocate

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


def _is_playlist_only(url: str) -> bool:
    """Return True when the URL points to a playlist without a specific video.

    youtube.com/watch?v=XYZ&list=PL... → False  (single video in playlist context)
    youtube.com/playlist?list=PL...   → True    (pure playlist)
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        # Pure playlist page: no v= param
        if "list" in params and "v" not in params:
            return True
    except Exception:
        pass
    return False


# ─── Pages ────────────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    return render_template("index.html")


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

    # Collect fingerprint + geo once — shared across all entries
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

    if _is_playlist_only(youtube_url):
        # ── Playlist: expand entries, one Download record per track ──
        entries = extract_playlist_entries(youtube_url)
        job_ids = []

        for entry in entries:
            track_url = entry["url"]
            record = Download(
                job_id="placeholder",
                youtube_url=track_url,
                playlist_url=youtube_url,
                hardware_model=hardware,
                identity_hash=identity,
                bot_score=bot,
                country_code=geo["country_code"],
                city=geo["city"],
                **meta,
            )
            db.session.add(record)
            db.session.flush()

            job_id = start_download(app_obj, track_url, download_dir)
            record.job_id = job_id
            job_ids.append(job_id)

        db.session.commit()
        return jsonify({"job_ids": job_ids}), 202

    else:
        # ── Single video (may have list= param for context — we ignore it) ──
        record = Download(
            job_id="placeholder",
            youtube_url=youtube_url,
            playlist_url=None,
            hardware_model=hardware,
            identity_hash=identity,
            bot_score=bot,
            country_code=geo["country_code"],
            city=geo["city"],
            **meta,
        )
        db.session.add(record)
        db.session.flush()

        job_id = start_download(app_obj, youtube_url, download_dir)
        record.job_id = job_id
        db.session.commit()

        return jsonify({"job_ids": [job_id]}), 202


@bp.route("/status/<job_id>")
def status(job_id: str):
    job = get_job(job_id)
    if job is None:
        # Fall back to DB (e.g. after server restart)
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
        "error_message": job.get("error"),
    }
    return jsonify(resp)


@bp.route("/files/<path:filename>")
def serve_file(filename: str):
    # Security: only serve files from the download dir, no path traversal
    safe_name = os.path.basename(filename)
    download_dir = current_app.config["DOWNLOAD_DIR"]

    # Files are stored as <job_id>.mp3; serve with track title via Content-Disposition
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


# ─── Error handlers ───────────────────────────────────────────────────────────

@bp.app_errorhandler(429)
def ratelimit_handler(e):
    return (
        jsonify({"error": "Too many requests. Please wait a moment before trying again."}),
        429,
    )
