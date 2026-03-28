"""Settings blueprint — /settings page + API token CRUD (v4.12.0)"""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request, session

from app import db
from app.auth_utils import user_required
from app.models import User
from app.player_models import ApiToken

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


# ── Page ─────────────────────────────────────────────────────────────────────

@settings_bp.route("/")
@user_required
def index():
    if request.args.get("fragment"):
        return render_template("fragments/settings.html")
    return render_template("shell.html", initial_fragment="settings")


# ── Profile API ──────────────────────────────────────────────────────────────

@settings_bp.route("/api/profile")
@user_required
def api_profile():
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "No session"}), 401
    user = User.query.get(email)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "provider": user.provider or "google",
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "is_admin": bool(user.is_admin),
    })


# ── Token CRUD ───────────────────────────────────────────────────────────────

@settings_bp.route("/api/tokens")
@user_required
def api_list_tokens():
    email = session.get("user_email")
    if not email:
        return jsonify([])
    tokens = (
        ApiToken.query
        .filter_by(user_email=email, is_active=True)
        .order_by(ApiToken.created_at.desc())
        .all()
    )
    return jsonify([
        {
            "id": t.id,
            "name": t.name,
            "token_prefix": t.token_prefix,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        }
        for t in tokens
    ])


@settings_bp.route("/api/tokens", methods=["POST"])
@user_required
def api_create_token():
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "No session"}), 401

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "El nombre es obligatorio."}), 400
    if len(name) > 128:
        return jsonify({"error": "Nombre demasiado largo (max 128)."}), 400

    # Limit: max 10 active tokens per user
    active_count = ApiToken.query.filter_by(user_email=email, is_active=True).count()
    if active_count >= 10:
        return jsonify({"error": "Maximo 10 tokens activos."}), 400

    raw, token_hash, token_prefix = ApiToken.generate()

    token = ApiToken(
        user_email=email,
        name=name,
        token_hash=token_hash,
        token_prefix=token_prefix,
    )
    db.session.add(token)
    db.session.commit()

    return jsonify({"ok": True, "token": raw, "id": token.id}), 201


@settings_bp.route("/api/tokens/<int:token_id>", methods=["DELETE"])
@user_required
def api_revoke_token(token_id: int):
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "No session"}), 401

    token = ApiToken.query.filter_by(id=token_id, user_email=email, is_active=True).first()
    if not token:
        return jsonify({"error": "Token no encontrado."}), 404

    token.is_active = False
    db.session.commit()
    return jsonify({"ok": True})
