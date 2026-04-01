import hashlib
import logging
import os
import uuid
import threading
import yt_dlp
from flask import current_app

logger = logging.getLogger("app")

# In-memory job store for quick status checks
# { job_id: {"status": "pending|done|error", "progress": 0-100, "error": ""} }
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# In-memory batch store for playlist downloads
# { batch_id: {"status": "...", "total": N, "completed": N, "failed": N, "skipped": N,
#               "title": "...", "tracks": [{"job_id": "...", "video_id": "...", "title": "...", "status": "..."}],
#               "app_playlist_id": None} }
_batches: dict[str, dict] = {}
_batches_lock = threading.Lock()

# Max concurrent downloads within a playlist batch
_BATCH_SEMAPHORE_SIZE = 3

# Max tracks allowed per playlist
PLAYLIST_MAX_TRACKS = 100


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def get_batch(batch_id: str) -> dict | None:
    with _batches_lock:
        return _batches.get(batch_id)


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


def _sha256(path: str) -> str:
    """Return the hex SHA-256 digest of a file, reading in 1 MB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _run_download(app, job_id: str, youtube_url: str, download_dir: str,
                  video_id: str | None = None, suppress_email: bool = False):
    """Background thread: download audio (or reuse deduped file) and update DB.

    Set *suppress_email* to True for batch (playlist) downloads where a single
    summary email is sent after the entire batch completes.
    """
    from app import db
    from app.models import Download

    with app.app_context():

        # ── v3.2.0: deduplication check ──────────────────────────────────────
        if video_id:
            existing = (
                Download.query
                .filter_by(video_id=video_id, status="done")
                .filter(Download.audio_hash.isnot(None))
                .order_by(Download.id.asc())
                .first()
            )
            if existing and existing.file_path and os.path.isfile(existing.file_path):
                # Reuse the existing file — no download needed
                with _jobs_lock:
                    _jobs[job_id]["status"]    = "done"
                    _jobs[job_id]["progress"]  = 100
                    _jobs[job_id]["file_name"] = existing.file_name
                    _jobs[job_id]["title"]     = existing.title
                    _jobs[job_id]["file_size"] = existing.file_size

                record = Download.query.filter_by(job_id=job_id).first()
                if record:
                    record.status     = "done"
                    record.file_path  = existing.file_path
                    record.file_name  = existing.file_name
                    record.title      = existing.title
                    record.file_size  = existing.file_size
                    record.audio_hash = existing.audio_hash
                    # video_id already set by routes.py before the flush
                    db.session.commit()
                return  # ← skip yt-dlp entirely

        # ── Normal download path ──────────────────────────────────────────────
        out_template = os.path.join(download_dir, f"{job_id}.%(ext)s")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            # Allow yt-dlp to download the EJS challenge solver from GitHub
            # so Deno can solve YouTube's signature/n-challenge (required 2026+)
            "remote_components": {"ejs:github"},
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

            mp3_path  = os.path.join(download_dir, f"{job_id}.mp3")
            file_name = f"{title}.mp3"
            file_size = os.path.getsize(mp3_path)
            audio_hash = _sha256(mp3_path)

            # Update in-memory
            with _jobs_lock:
                _jobs[job_id]["status"]    = "done"
                _jobs[job_id]["progress"]  = 100
                _jobs[job_id]["file_name"] = file_name
                _jobs[job_id]["title"]     = title
                _jobs[job_id]["file_size"] = file_size

            # Update DB — snapshot before commit to avoid post-expiry reloads
            record = Download.query.filter_by(job_id=job_id).first()
            if record:
                record.status     = "done"
                record.file_path  = mp3_path
                record.file_name  = file_name
                record.title      = title
                record.file_size  = file_size
                record.audio_hash = audio_hash
                # video_id already set by routes.py

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

                db.session.commit()

                if not suppress_email:
                    from app.mailer import send_download_notification
                    send_download_notification(mail_data)

        except Exception as exc:
            err = str(exc)
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"]  = err

            try:
                record = Download.query.filter_by(job_id=job_id).first()
                if record:
                    record.status        = "error"
                    record.error_message = err
                    db.session.commit()
            except Exception:
                pass


def start_download(app, youtube_url: str, download_dir: str,
                   video_id: str | None = None) -> str:
    """Create a job, start background thread, return job_id."""
    job_id = str(uuid.uuid4())

    with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "progress": 0}

    t = threading.Thread(
        target=_run_download,
        args=(app, job_id, youtube_url, download_dir),
        kwargs={"video_id": video_id},
        daemon=True,
    )
    t.start()
    return job_id


# ── Playlist batch support (v5.0.0) ─────────────────────────────────────────


def extract_playlist(youtube_url: str) -> dict:
    """Extract metadata for a YouTube playlist without downloading.

    Returns ``{"title": "...", "entries": [{"id": "...", "title": "..."}, ...]}``
    or raises on failure.
    """
    ydl_opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        # Allow yt-dlp to download the EJS challenge solver
        "remote_components": {"ejs:github"},
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)

    title = info.get("title") or "YouTube Playlist"
    entries = []
    for entry in (info.get("entries") or []):
        if entry is None:
            continue
        vid = entry.get("id") or entry.get("url")
        if not vid:
            continue
        entries.append({
            "id": vid,
            "title": entry.get("title") or vid,
        })
    return {"title": title, "entries": entries}


def _run_batch_download(app, batch_id: str, entries: list[dict],
                        download_dir: str, user_email: str | None):
    """Orchestrator thread for a playlist batch download.

    Uses a semaphore to limit concurrency to *_BATCH_SEMAPHORE_SIZE* tracks.
    After all tracks finish, auto-creates an in-app Playlist and sends a
    summary email.
    """
    from app import db
    from app.models import Download, PlaylistBatch
    from app.player_models import Playlist, PlaylistTrack

    sem = threading.Semaphore(_BATCH_SEMAPHORE_SIZE)
    track_threads: list[threading.Thread] = []

    # Track results — (position, job_id, status, title)
    results: list[dict] = []
    results_lock = threading.Lock()

    def _download_one(pos: int, entry: dict, job_id: str):
        """Worker: download a single track within the batch."""
        video_id = entry["id"]
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        sem.acquire()
        try:
            # Update per-track status in batch store
            with _batches_lock:
                for t in _batches[batch_id]["tracks"]:
                    if t["job_id"] == job_id:
                        t["status"] = "downloading"
                        break

            # Reuse the existing _run_download (runs synchronously in this thread)
            _run_download(
                app, job_id, video_url, download_dir,
                video_id=video_id, suppress_email=True,
            )

            # Check final status from in-memory store
            with _jobs_lock:
                job = _jobs.get(job_id, {})
                final_status = job.get("status", "error")
                final_title = job.get("title", entry["title"])

            # Determine if it was a dedup skip
            was_skipped = False
            with app.app_context():
                rec = Download.query.filter_by(job_id=job_id).first()
                if rec and rec.status == "done":
                    # Check if the file_path points to a different job_id's file
                    # (dedup reuses an existing file)
                    if rec.file_path and not rec.file_path.endswith(f"{job_id}.mp3"):
                        was_skipped = True

            with results_lock:
                results.append({
                    "pos": pos, "job_id": job_id,
                    "status": final_status, "title": final_title,
                    "skipped": was_skipped,
                })

            # Update batch progress
            with _batches_lock:
                batch = _batches[batch_id]
                for t in batch["tracks"]:
                    if t["job_id"] == job_id:
                        t["status"] = final_status
                        t["title"] = final_title
                        break
                if final_status == "done":
                    if was_skipped:
                        batch["skipped"] += 1
                    batch["completed"] += 1
                else:
                    batch["failed"] += 1

        except Exception as exc:
            logger.warning("batch %s track %s error: %s", batch_id, job_id, exc)
            with _batches_lock:
                batch = _batches[batch_id]
                for t in batch["tracks"]:
                    if t["job_id"] == job_id:
                        t["status"] = "error"
                        break
                batch["failed"] += 1
            with results_lock:
                results.append({
                    "pos": pos, "job_id": job_id,
                    "status": "error", "title": entry["title"],
                    "skipped": False,
                })
        finally:
            sem.release()

    with app.app_context():
        # ── Create Download rows and spawn worker threads ────────────────────
        for pos, entry in enumerate(entries):
            job_id = str(uuid.uuid4())
            video_id = entry["id"]

            # Create the Download DB row
            record = Download(
                job_id=job_id,
                youtube_url=f"https://www.youtube.com/watch?v={video_id}",
                status="pending",
                user_email=user_email,
                video_id=video_id,
                batch_id=batch_id,
            )
            db.session.add(record)

            # Register in in-memory job store
            with _jobs_lock:
                _jobs[job_id] = {"status": "pending", "progress": 0}

            # Register track in batch store
            with _batches_lock:
                _batches[batch_id]["tracks"].append({
                    "job_id": job_id,
                    "video_id": video_id,
                    "title": entry["title"],
                    "status": "queued",
                })

            t = threading.Thread(
                target=_download_one, args=(pos, entry, job_id),
                daemon=True,
            )
            track_threads.append(t)

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.error("batch %s: failed to create Download rows", batch_id)
            with _batches_lock:
                _batches[batch_id]["status"] = "error"
            return

        # Update batch status to downloading
        with _batches_lock:
            _batches[batch_id]["status"] = "downloading"
        try:
            batch_rec = PlaylistBatch.query.filter_by(batch_id=batch_id).first()
            if batch_rec:
                batch_rec.status = "downloading"
                db.session.commit()
        except Exception:
            db.session.rollback()

        # ── Start all worker threads (semaphore limits concurrency) ──────────
        for t in track_threads:
            t.start()

        # Wait for all to complete
        for t in track_threads:
            t.join(timeout=600)  # 10 min max per track

        # ── Finalize: create in-app playlist + update DB ─────────────────────
        sorted_results = sorted(results, key=lambda r: r["pos"])
        done_jobs = [r for r in sorted_results if r["status"] == "done"]
        total_failed = sum(1 for r in sorted_results if r["status"] == "error")
        total_skipped = sum(1 for r in sorted_results if r.get("skipped"))

        app_playlist_id = None
        if done_jobs and user_email:
            # Auto-create an in-app playlist
            with _batches_lock:
                pl_title = _batches[batch_id].get("title", "YouTube Playlist")
            try:
                pl = Playlist(name=pl_title, user_email=user_email)
                db.session.add(pl)
                db.session.flush()

                for i, r in enumerate(done_jobs):
                    pt = PlaylistTrack(
                        playlist_id=pl.id,
                        job_id=r["job_id"],
                        position=i,
                        added_by=user_email,
                    )
                    db.session.add(pt)

                # Also add owner as PlaylistMember
                from app.player_models import PlaylistMember
                db.session.add(PlaylistMember(
                    playlist_id=pl.id,
                    user_email=user_email,
                    role="owner",
                ))

                db.session.commit()
                app_playlist_id = pl.id
            except Exception:
                db.session.rollback()
                logger.error("batch %s: failed to create in-app playlist", batch_id)

        # Determine final batch status
        final_status = "done"
        if len(done_jobs) == 0 and total_failed > 0:
            final_status = "error"

        # Update in-memory batch
        with _batches_lock:
            batch = _batches[batch_id]
            batch["status"] = final_status
            batch["app_playlist_id"] = app_playlist_id
            batch["completed"] = len(done_jobs)
            batch["failed"] = total_failed
            batch["skipped"] = total_skipped

        # Update DB
        try:
            batch_rec = PlaylistBatch.query.filter_by(batch_id=batch_id).first()
            if batch_rec:
                batch_rec.status = final_status
                batch_rec.completed = len(done_jobs)
                batch_rec.failed = total_failed
                batch_rec.skipped = total_skipped
                batch_rec.app_playlist_id = app_playlist_id
                if final_status == "error" and total_failed == len(entries):
                    batch_rec.error_message = "All tracks failed to download"
                db.session.commit()
        except Exception:
            db.session.rollback()

        # ── Send batch summary email ─────────────────────────────────────────
        try:
            batch_rec = PlaylistBatch.query.filter_by(batch_id=batch_id).first()
            if batch_rec:
                from app.mailer import send_download_notification
                send_download_notification({
                    "job_id":        f"batch:{batch_id}",
                    "title":         f"[Playlist] {batch_rec.playlist_title} ({len(done_jobs)}/{len(entries)} tracks)",
                    "file_name":     f"{len(done_jobs)} tracks downloaded",
                    "youtube_url":   batch_rec.youtube_url,
                    "created_at":    batch_rec.created_at,
                    "ip_address":    batch_rec.ip_address,
                    "country_code":  batch_rec.country_code,
                    "city":          batch_rec.city,
                    "ua_browser":    None,
                    "ua_browser_version": None,
                    "ua_os":         None,
                    "ua_device":     None,
                    "accept_language": None,
                    "fingerprint_hash": batch_rec.fingerprint_hash,
                    "bot_score":     None,
                })
        except Exception:
            logger.warning("batch %s: failed to send summary email", batch_id)

        logger.info(
            "batch %s finished: %d done, %d failed, %d skipped out of %d",
            batch_id, len(done_jobs), total_failed, total_skipped, len(entries),
        )


def start_playlist_download(app, batch_id: str, entries: list[dict],
                            download_dir: str, user_email: str | None,
                            playlist_title: str = "YouTube Playlist") -> None:
    """Register a batch in-memory and spawn the orchestrator thread."""
    with _batches_lock:
        _batches[batch_id] = {
            "status": "downloading",
            "title": playlist_title,
            "total": len(entries),
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "tracks": [],
            "app_playlist_id": None,
        }

    t = threading.Thread(
        target=_run_batch_download,
        args=(app, batch_id, entries, download_dir, user_email),
        daemon=True,
    )
    t.start()
