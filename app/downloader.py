import os
import uuid
import threading
import yt_dlp
from flask import current_app

# In-memory job store for quick status checks
# { job_id: {"status": "pending|done|error", "progress": 0-100, "error": ""} }
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _progress_hook(job_id: str):
    def hook(d):
        with _jobs_lock:
            if job_id not in _jobs:
                return
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    pct = int(downloaded / total * 90)  # cap at 90, post-process adds 10
                    # Never go backwards — post-processing fires new downloading
                    # events with downloaded_bytes=0 which would reset the bar
                    if pct > _jobs[job_id].get("progress", 0):
                        _jobs[job_id]["progress"] = pct
            elif d["status"] == "finished":
                _jobs[job_id]["progress"] = 95
    return hook


def _run_download(app, job_id: str, youtube_url: str, download_dir: str):
    """Background thread: download audio and update DB + in-memory job store."""
    from app import db
    from app.models import Download

    with app.app_context():
        out_template = os.path.join(download_dir, f"{job_id}.%(ext)s")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",  # best VBR quality
                }
            ],
            "progress_hooks": [_progress_hook(job_id)],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=True)
                title = info.get("title", job_id)

            mp3_path = os.path.join(download_dir, f"{job_id}.mp3")
            file_name = f"{title}.mp3"

            # Update in-memory
            with _jobs_lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["progress"] = 100
                _jobs[job_id]["file_name"] = file_name
                _jobs[job_id]["title"] = title
                _jobs[job_id]["file_size"] = os.path.getsize(mp3_path)

            # Update DB — capture all fields BEFORE commit so that post-commit
            # attribute expiry (SQLAlchemy default) never causes a lazy-reload
            # failure when we pass values to the mailer thread.
            record = Download.query.filter_by(job_id=job_id).first()
            if record:
                record.status    = "done"
                record.file_path = mp3_path
                record.file_name = file_name
                record.title     = title
                record.file_size = os.path.getsize(mp3_path)

                # Snapshot every field we need while the session is still live
                mail_data = {
                    "job_id":             record.job_id,
                    "title":              title,
                    "file_name":          file_name,
                    "youtube_url":        record.youtube_url,
                    "created_at":         record.created_at,
                    "ip_address":         record.ip_address,
                    "country_code":       record.country_code,
                    "city":               record.city,
                    "ua_browser":         record.ua_browser,
                    "ua_browser_version": record.ua_browser_version,
                    "ua_os":              record.ua_os,
                    "ua_device":          record.ua_device,
                    "accept_language":    record.accept_language,
                    "fingerprint_hash":   record.fingerprint_hash,
                    "bot_score":          record.bot_score,
                }

                db.session.commit()  # expires ORM attrs — safe, we already snapshotted

                from app.mailer import send_download_notification
                send_download_notification(mail_data)

        except Exception as exc:
            err = str(exc)
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = err

            # Guard against a broken/rolled-back session in the error path —
            # an uncaught secondary exception here would leave the job stuck
            # at "pending" forever in the in-memory store.
            try:
                record = Download.query.filter_by(job_id=job_id).first()
                if record:
                    record.status = "error"
                    record.error_message = err
                    db.session.commit()
            except Exception:
                pass


def start_download(app, youtube_url: str, download_dir: str) -> str:
    """Create a job, start background thread, return job_id."""
    job_id = str(uuid.uuid4())

    with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "progress": 0}

    t = threading.Thread(
        target=_run_download,
        args=(app, job_id, youtube_url, download_dir),
        daemon=True,
    )
    t.start()
    return job_id
