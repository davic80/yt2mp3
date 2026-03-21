import os
import re
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
from app.downloader import start_download, get_job
from app.fingerprint import collect
from app.hardware_parser import detect_hardware, compute_identity_hash

bp = Blueprint("main", __name__)

YOUTUBE_RE = re.compile(
    r"^(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|embed/)|youtu\.be/)"
    r"[\w\-]{11}"
)


def _rate_limits():
    per_hour = current_app.config.get("RATE_LIMIT_PER_HOUR", "10")
    per_minute = current_app.config.get("RATE_LIMIT_PER_MINUTE", "3")
    return [f"{per_minute} per minute", f"{per_hour} per hour"]


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
        return jsonify({"error": "URL requerida"}), 400

    if not YOUTUBE_RE.match(youtube_url):
        return jsonify({"error": "URL de YouTube no válida"}), 400

    # Collect fingerprint data
    meta = collect(
        client_fingerprint=data.get("fingerprint"),
        client_cookies=data.get("cookies"),
    )

    # Create DB record
    fp_components = meta.get("fingerprint_components")
    record = Download(
        job_id="placeholder",  # will be replaced below
        youtube_url=youtube_url,
        hardware_model=detect_hardware(fp_components),
        identity_hash=compute_identity_hash(fp_components),
        **meta,
    )
    db.session.add(record)
    db.session.flush()  # get the id

    # Start background download
    job_id = start_download(
        current_app._get_current_object(),
        youtube_url,
        current_app.config["DOWNLOAD_DIR"],
    )

    record.job_id = job_id
    db.session.commit()

    return jsonify({"job_id": job_id}), 202


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

    # Find the UUID-named mp3 from the job_id prefix in filename
    # Files are stored as <job_id>.mp3; we serve them with the track title
    # via Content-Disposition header
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
        jsonify(
            {
                "error": (
                    "Demasiadas solicitudes. "
                    "Espera un momento antes de volver a intentarlo."
                )
            }
        ),
        429,
    )
