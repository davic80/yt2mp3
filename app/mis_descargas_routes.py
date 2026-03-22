"""
/mis-descargas blueprint — personal download history for logged-in users.

Endpoints:
  GET  /mis-descargas/              → page (requires login)
  GET  /mis-descargas/api/tracks    → JSON list of user's done downloads
  PATCH /mis-descargas/api/tracks/<job_id>  → rename title / file_name
  DELETE /mis-descargas/api/tracks/<job_id> → delete record (+ file if no other record uses it)
  GET  /mis-descargas/api/tracks/zip → download all (or ?job_ids=a,b,c) as ZIP
"""
import os
import io
import re
import zipfile

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app import db
from app.auth_utils import get_current_user_email, user_required
from app.models import Download

mis_bp = Blueprint("mis", __name__, url_prefix="/mis-descargas")


def _own_done_track(job_id: str, email: str) -> Download:
    """Return the Download or 404/403.  Always enforces ownership."""
    record = Download.query.filter_by(job_id=job_id, status="done").first_or_404()
    if email and record.user_email != email:
        abort(403)
    return record


# ── Page ──────────────────────────────────────────────────────────────────────

@mis_bp.route("/")
@user_required
def index():
    return render_template("mis_descargas.html")


# ── List ──────────────────────────────────────────────────────────────────────

@mis_bp.route("/api/tracks")
@user_required
def api_tracks():
    email = get_current_user_email()
    query = Download.query.filter_by(status="done")
    if email:
        query = query.filter_by(user_email=email)
    rows = query.order_by(Download.created_at.desc()).all()
    return jsonify([
        {
            "job_id":     r.job_id,
            "title":      r.title or r.job_id,
            "file_name":  r.file_name,
            "file_size":  r.file_size,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "youtube_url": r.youtube_url,
        }
        for r in rows
    ])


# ── Rename ────────────────────────────────────────────────────────────────────

@mis_bp.route("/api/tracks/<job_id>", methods=["PATCH"])
@user_required
def api_rename(job_id: str):
    email = get_current_user_email()
    record = _own_done_track(job_id, email)

    data = request.get_json(silent=True) or {}
    new_title = (data.get("title") or "").strip()
    if not new_title:
        return jsonify({"error": "title required"}), 400

    # Sanitize for use as a filename: strip path separators and control chars
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", new_title).strip()
    if not safe_title:
        return jsonify({"error": "title contains only invalid characters"}), 400

    record.title     = safe_title
    record.file_name = f"{safe_title}.mp3"
    db.session.commit()

    return jsonify({"title": record.title, "file_name": record.file_name})


# ── Delete ────────────────────────────────────────────────────────────────────

@mis_bp.route("/api/tracks/<job_id>", methods=["DELETE"])
@user_required
def api_delete(job_id: str):
    email = get_current_user_email()
    record = _own_done_track(job_id, email)

    file_path = record.file_path  # may be None or shared

    db.session.delete(record)
    db.session.flush()  # execute DELETE before we count remaining refs

    # Reference-count: only remove the file if no other Download row uses it
    if file_path:
        remaining = Download.query.filter_by(file_path=file_path).count()
        if remaining == 0:
            try:
                os.remove(file_path)
            except OSError:
                pass  # already gone — not an error

    db.session.commit()
    return jsonify({"ok": True})


# ── ZIP download ──────────────────────────────────────────────────────────────

@mis_bp.route("/api/tracks/zip")
@user_required
def api_zip():
    email = get_current_user_email()

    # Optional ?job_ids=a,b,c to zip a subset; otherwise zip everything
    raw_ids = request.args.get("job_ids", "").strip()
    if raw_ids:
        job_ids = [j.strip() for j in raw_ids.split(",") if j.strip()]
        query = Download.query.filter(
            Download.job_id.in_(job_ids),
            Download.status == "done",
        )
        if email:
            query = query.filter_by(user_email=email)
        rows = query.all()
    else:
        query = Download.query.filter_by(status="done")
        if email:
            query = query.filter_by(user_email=email)
        rows = query.order_by(Download.created_at.desc()).all()

    if not rows:
        return jsonify({"error": "no tracks found"}), 404

    buf = io.BytesIO()
    seen_names: dict[str, int] = {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            if not r.file_path or not os.path.isfile(r.file_path):
                continue
            base_name = r.file_name or f"{r.job_id}.mp3"
            # Deduplicate filenames inside the ZIP
            if base_name in seen_names:
                seen_names[base_name] += 1
                stem, ext = os.path.splitext(base_name)
                arc_name = f"{stem} ({seen_names[base_name]}){ext}"
            else:
                seen_names[base_name] = 0
                arc_name = base_name
            zf.write(r.file_path, arc_name)

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="mis-descargas.zip",
    )
