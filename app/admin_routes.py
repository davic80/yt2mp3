import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
)

from app import db
from app.models import Download, User
from app.auth_utils import admin_or_local

admin_bp = Blueprint("admin", __name__, url_prefix="/db")

_VALID_PER_PAGE = (10, 25, 50, 100)


def _get_per_page() -> int:
    per_page = request.args.get("per_page", 25, type=int)
    return per_page if per_page in _VALID_PER_PAGE else 25


# ── Pages ──────────────────────────────────────────────────────────────────────

@admin_bp.route("/")
@admin_or_local
def index():
    page = request.args.get("page", 1, type=int)
    per_page = _get_per_page()
    user_filter = request.args.get("user", "").strip()
    query = Download.query.order_by(Download.created_at.desc())
    if user_filter:
        query = query.filter(Download.user_email == user_filter)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return render_template("admin/index.html", pagination=pagination, per_page=per_page, user_filter=user_filter)


@admin_bp.route("/table-fragment")
@admin_or_local
def table_fragment():
    """Returns only the table HTML fragment for AJAX refresh."""
    page = request.args.get("page", 1, type=int)
    per_page = _get_per_page()
    user_filter = request.args.get("user", "").strip()
    query = Download.query.order_by(Download.created_at.desc())
    if user_filter:
        query = query.filter(Download.user_email == user_filter)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return render_template("admin/_table.html", pagination=pagination, per_page=per_page, user_filter=user_filter)


@admin_bp.route("/analytics")
@admin_or_local
def analytics():
    from sqlalchemy import func, text as sa_text

    # ── Downloads per day ────────────────────────────────────────────────────
    daily_rows = (
        db.session.query(
            func.date(Download.created_at).label("day"),
            func.count(Download.id).label("cnt"),
        )
        .group_by(func.date(Download.created_at))
        .order_by(func.date(Download.created_at))
        .all()
    )
    daily_labels = [r.day for r in daily_rows]
    daily_counts = [r.cnt for r in daily_rows]

    # ── Top 10 songs (by title, only done jobs with a title) ─────────────────
    song_rows = (
        db.session.query(Download.title, func.count(Download.id).label("cnt"))
        .filter(Download.status == "done", Download.title != None)  # noqa: E711
        .group_by(Download.title)
        .order_by(func.count(Download.id).desc())
        .limit(10)
        .all()
    )
    song_labels = [r.title for r in song_rows]
    song_counts = [r.cnt for r in song_rows]

    # ── Top 10 countries ─────────────────────────────────────────────────────
    country_rows = (
        db.session.query(Download.country_code, func.count(Download.id).label("cnt"))
        .filter(Download.country_code != None)  # noqa: E711
        .group_by(Download.country_code)
        .order_by(func.count(Download.id).desc())
        .limit(10)
        .all()
    )
    country_labels = [r.country_code for r in country_rows]
    country_counts = [r.cnt for r in country_rows]

    # ── Summary stats ────────────────────────────────────────────────────────
    total = Download.query.count()
    total_done = Download.query.filter_by(status="done").count()
    total_error = Download.query.filter_by(status="error").count()

    # ── Top 10 downloaders ───────────────────────────────────────────────────
    user_rows = (
        db.session.query(
            Download.user_email,
            func.count(Download.id).label("cnt"),
        )
        .filter(Download.user_email != None, Download.status == "done")  # noqa: E711
        .group_by(Download.user_email)
        .order_by(func.count(Download.id).desc())
        .limit(10)
        .all()
    )
    # Resolve emails → display names
    user_emails = [r.user_email for r in user_rows]
    name_map = {}
    if user_emails:
        for u in User.query.filter(User.email.in_(user_emails)).all():
            name_map[u.email] = u.name or u.email
    user_labels = [name_map.get(r.user_email, r.user_email) for r in user_rows]
    user_counts = [r.cnt for r in user_rows]

    return render_template(
        "admin/analytics.html",
        daily_labels=json.dumps(daily_labels),
        daily_counts=json.dumps(daily_counts),
        song_labels=json.dumps(song_labels),
        song_counts=json.dumps(song_counts),
        country_labels=json.dumps(country_labels),
        country_counts=json.dumps(country_counts),
        user_labels=json.dumps(user_labels),
        user_counts=json.dumps(user_counts),
        total=total,
        total_done=total_done,
        total_error=total_error,
    )


# ── ZIP download ───────────────────────────────────────────────────────────────

@admin_bp.route("/download-zip", methods=["POST"])
@admin_or_local
def download_zip():
    data = request.get_json(silent=True) or {}
    job_ids = data.get("job_ids", [])
    if not job_ids:
        return jsonify({"error": "no job_ids provided"}), 400

    records = (
        Download.query
        .filter(Download.job_id.in_(job_ids), Download.status == "done")
        .all()
    )
    if not records:
        return jsonify({"error": "no downloadable files in selection"}), 400

    buf = io.BytesIO()
    seen_names: dict[str, int] = {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in records:
            base_name = (r.file_name or r.job_id) if not (r.file_name or "").endswith(".mp3") \
                        else (r.file_name or r.job_id)[:-4]
            candidate = f"{base_name}.mp3"
            if candidate in seen_names:
                seen_names[candidate] += 1
                candidate = f"{base_name} ({seen_names[candidate]}).mp3"
            else:
                seen_names[candidate] = 1
            try:
                zf.write(r.file_path, arcname=candidate)
            except Exception:
                pass  # skip files that can't be read (shouldn't happen)

    buf.seek(0)
    date_str = datetime.now().strftime("%Y-%m-%d")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"yt2mp3-{date_str}.zip",
    )


# ── Rename title ───────────────────────────────────────────────────────────────

@admin_bp.route("/rename", methods=["POST"])
@admin_or_local
def rename_record():
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id", "").strip()
    new_title = data.get("title", "").strip()
    if not job_id or not new_title:
        return jsonify({"error": "job_id and title are required"}), 400

    record = Download.query.filter_by(job_id=job_id).first_or_404()
    record.title     = new_title
    record.file_name = new_title + ".mp3"
    db.session.commit()
    return jsonify({"ok": True, "title": record.title, "file_name": record.file_name})


# ── Delete records ─────────────────────────────────────────────────────────────

@admin_bp.route("/delete", methods=["POST"])
@admin_or_local
def delete_records():
    data = request.get_json(silent=True) or {}
    job_ids = data.get("job_ids", [])
    if not job_ids:
        return jsonify({"error": "no job_ids provided"}), 400

    records = Download.query.filter(Download.job_id.in_(job_ids)).all()
    deleted = 0
    for r in records:
        if r.file_path:
            try:
                os.remove(r.file_path)
            except OSError:
                pass
        db.session.delete(r)
        deleted += 1
    db.session.commit()
    return jsonify({"deleted": deleted})


# ── Users management ──────────────────────────────────────────────────────────

@admin_bp.route("/users")
@admin_or_local
def admin_users():
    return render_template("admin/users.html")


@admin_bp.route("/api/users")
@admin_or_local
def api_users():
    from app.player_models import UserFeature, PlayEvent
    from sqlalchemy import func

    rows = (
        db.session.query(
            User,
            func.count(Download.job_id.distinct()).label("track_count"),
            func.count(PlayEvent.id).label("play_count"),
            func.sum(PlayEvent.seconds_played).label("seconds_total"),
            func.max(PlayEvent.played_at).label("last_play"),
            UserFeature.lyrics_enabled,
            UserFeature.share_enabled,
        )
        .outerjoin(Download,  Download.user_email  == User.email)
        .outerjoin(PlayEvent, PlayEvent.user_email == User.email)
        .outerjoin(UserFeature, UserFeature.user_email == User.email)
        .group_by(User.email)
        .order_by(func.count(PlayEvent.id).desc())
        .all()
    )

    return jsonify([
        {
            "email":           u.email,
            "name":            u.name or "",
            "picture":         u.picture or "",
            "provider":        u.provider or "local",
            "created_at":      u.created_at.isoformat() if u.created_at else None,
            "track_count":     tc or 0,
            "play_count":      pc or 0,
            "minutes_played":  round((sec or 0) / 60, 1),
            "last_play":       lp.isoformat() if lp else None,
            "lyrics_enabled":  bool(le) if le is not None else False,
            "share_enabled":   bool(se) if se is not None else False,
            "is_admin":        bool(u.is_admin),
            "is_enabled":      bool(u.is_enabled) if u.is_enabled is not None else True,
        }
        for u, tc, pc, sec, lp, le, se in rows
    ])


@admin_bp.route("/api/users/<path:email>", methods=["DELETE"])
@admin_or_local
def api_delete_user(email: str):
    """Delete a user and all associated data. Downloads are kept as anonymous."""
    from app.player_models import Playlist, PlaylistShare, PlaylistMember, UserFeature, PlayEvent

    user = User.query.get(email)
    if not user:
        return jsonify({"error": "Usuario no encontrado."}), 404

    # Prevent deleting the last admin
    if user.is_admin:
        admin_count = User.query.filter_by(is_admin=True).count()
        if admin_count <= 1:
            return jsonify({"error": "No se puede eliminar el último administrador."}), 400

    # 1. Delete playlist shares for user's playlists
    pl_ids = [p.id for p in Playlist.query.filter_by(user_email=email).all()]
    if pl_ids:
        PlaylistShare.query.filter(PlaylistShare.playlist_id.in_(pl_ids)).delete(
            synchronize_session=False
        )
        PlaylistMember.query.filter(PlaylistMember.playlist_id.in_(pl_ids)).delete(
            synchronize_session=False
        )
    # 2. Delete playlists (cascade deletes PlaylistTrack rows)
    Playlist.query.filter_by(user_email=email).delete(synchronize_session=False)
    # 2b. Remove user from collaborative playlists they are a member of
    PlaylistMember.query.filter_by(user_email=email).delete(synchronize_session=False)
    # 3. Delete user features
    UserFeature.query.filter_by(user_email=email).delete(synchronize_session=False)
    # 4. Delete play events
    PlayEvent.query.filter_by(user_email=email).delete(synchronize_session=False)
    # 5. De-associate downloads (keep as anonymous)
    Download.query.filter_by(user_email=email).update(
        {"user_email": None}, synchronize_session=False
    )
    # 6. Delete user
    db.session.delete(user)
    db.session.commit()

    return jsonify({"ok": True, "email": email})


@admin_bp.route("/api/users/<path:email>/features", methods=["POST"])
@admin_or_local
def api_set_user_features(email: str):
    from app.player_models import UserFeature

    data = request.get_json(silent=True) or {}

    # Handle UserFeature toggles (lyrics, share)
    feat = UserFeature.query.filter_by(user_email=email).first()
    if not feat:
        feat = UserFeature(user_email=email, lyrics_enabled=False, share_enabled=False)
        db.session.add(feat)

    if "lyrics_enabled" in data:
        feat.lyrics_enabled = bool(data["lyrics_enabled"])
    if "share_enabled" in data:
        feat.share_enabled = bool(data["share_enabled"])

    # Handle User model toggles (is_admin, is_enabled)
    user = User.query.get(email)
    if user:
        if "is_admin" in data:
            # Prevent demoting the last admin
            if user.is_admin and not bool(data["is_admin"]):
                admin_count = User.query.filter_by(is_admin=True).count()
                if admin_count <= 1:
                    return jsonify({"error": "No se puede quitar admin al último administrador."}), 400
            user.is_admin = bool(data["is_admin"])
        if "is_enabled" in data:
            user.is_enabled = bool(data["is_enabled"])

    db.session.commit()
    result = {
        "ok": True,
        "lyrics_enabled": feat.lyrics_enabled,
        "share_enabled": feat.share_enabled,
    }
    if user:
        result["is_admin"] = bool(user.is_admin)
        result["is_enabled"] = bool(user.is_enabled)
    return jsonify(result)
