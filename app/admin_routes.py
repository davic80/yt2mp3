import base64
import json
import re
import time
import functools

import webauthn
from webauthn.helpers.structs import (
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
)
from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    abort,
)
from app import db
from app.admin_models import AdminUser, WebAuthnCredential, WebAuthnChallenge
from app.models import Download

admin_bp = Blueprint("admin", __name__, url_prefix="/db")

CHALLENGE_TTL = 300  # 5 minutes

# RFC-1918 + loopback ranges — registration is only allowed from these
_LOCAL_EXACT = {"127.0.0.1", "::1", "localhost"}
_LOCAL_RE = [
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^fd"),   # IPv6 ULA
    re.compile(r"^fe80"), # IPv6 link-local
]


# ── Network helpers ────────────────────────────────────────────────────────────

def _client_ip() -> str:
    """Real client IP — prefers Cloudflare header, then X-Forwarded-For."""
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or ""
    )

def _is_local_request() -> bool:
    """Return True only if the request originates from a private/loopback address."""
    ip = _client_ip()
    if ip in _LOCAL_EXACT:
        return True
    return any(pattern.match(ip) for pattern in _LOCAL_RE)


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _rp_id():
    return current_app.config.get("WEBAUTHN_RP_ID", "localhost")

def _rp_name():
    return current_app.config.get("WEBAUTHN_RP_NAME", "yt2mp3 admin")

def _origin():
    return current_app.config.get("WEBAUTHN_ORIGIN", "http://localhost:5000")

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))

def _clean_challenges():
    """Remove expired challenges."""
    WebAuthnChallenge.query.filter(WebAuthnChallenge.expires_at < time.time()).delete()
    db.session.commit()

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin.login_page"))
        return f(*args, **kwargs)
    return decorated

def local_only(f):
    """Decorator: reject with 403 if request is not from a local network address."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _is_local_request():
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Pages ──────────────────────────────────────────────────────────────────────

@admin_bp.route("/")
@local_only
@login_required
def index():
    page = request.args.get("page", 1, type=int)
    per_page = 25
    pagination = (
        Download.query
        .order_by(Download.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return render_template("admin/index.html", pagination=pagination)


@admin_bp.route("/login")
@local_only
def login_page():
    if session.get("admin_authenticated"):
        return redirect(url_for("admin.index"))
    has_credentials = WebAuthnCredential.query.count() > 0
    return render_template(
        "admin/login.html",
        has_credentials=has_credentials,
    )


@admin_bp.route("/logout", methods=["POST"])
@local_only
def logout():
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin.login_page"))


# ── WebAuthn: Authentication (local only) ─────────────────────────────────────

@admin_bp.route("/webauthn/auth/begin", methods=["POST"])
@local_only
def auth_begin():
    _clean_challenges()

    user = AdminUser.query.filter_by(username="admin").first()
    if not user or not user.credentials:
        return jsonify({"error": "No passkeys registered"}), 400

    allow = [
        PublicKeyCredentialDescriptor(
            id=c.credential_id_bytes(),
            transports=json.loads(c.transports or "[]"),
        )
        for c in user.credentials
    ]

    options = webauthn.generate_authentication_options(
        rp_id=_rp_id(),
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    challenge_b64 = _b64url(options.challenge)
    ch = WebAuthnChallenge(
        ceremony="authentication",
        challenge=challenge_b64,
        expires_at=time.time() + CHALLENGE_TTL,
    )
    db.session.add(ch)
    db.session.commit()

    return jsonify(json.loads(webauthn.options_to_json(options)))


@admin_bp.route("/webauthn/auth/complete", methods=["POST"])
@local_only
def auth_complete():
    _clean_challenges()
    data = request.get_json()

    ch = (
        WebAuthnChallenge.query
        .filter_by(ceremony="authentication")
        .order_by(WebAuthnChallenge.id.desc())
        .first_or_404()
    )

    # Find credential by ID — normalise base64url padding
    raw_id = data.get("rawId") or data.get("id")
    cred_id_b64 = _b64url(_b64url_decode(raw_id))
    cred = WebAuthnCredential.query.filter_by(credential_id=cred_id_b64).first()
    if not cred:
        cred = WebAuthnCredential.query.filter(
            WebAuthnCredential.credential_id.like(raw_id[:20] + "%")
        ).first()
    if not cred:
        return jsonify({"error": "Credential not found"}), 400

    try:
        verification = webauthn.verify_authentication_response(
            credential=data,
            expected_challenge=_b64url_decode(ch.challenge),
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            credential_public_key=cred.public_key_bytes(),
            credential_current_sign_count=cred.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    db.session.delete(ch)
    cred.sign_count = verification.new_sign_count
    db.session.commit()

    session["admin_authenticated"] = True
    session.permanent = True
    return jsonify({"ok": True})
