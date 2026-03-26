"""Auth blueprint — /auth/login (form + Google), /auth/callback, /auth/logout, /auth/me

v4.5.0: replaced Auth0 with direct Google OAuth via Authlib.
v4.9.0: added local password login (email + password form) alongside Google OAuth.
Env vars required:
  GOOGLE_CLIENT_ID      — OAuth 2.0 client ID from Google Cloud Console
  GOOGLE_CLIENT_SECRET  — OAuth 2.0 client secret
  GOOGLE_CALLBACK_URL   — full callback URL, e.g. https://yt2mp3.f1madrid.win/auth/callback
"""
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app import db
from app.models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _oauth():
    """Lazy import so the OAuth object is only used after app init."""
    from app import oauth as _oauth_obj
    return _oauth_obj


# ── Login (GET = show form, POST = password auth) ────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.args.get("next") or request.form.get("next") or "/"

    if request.method == "GET":
        session["next"] = next_url
        error = request.args.get("error")
        return render_template("auth/login.html", next_url=next_url, error=error)

    # POST — email + password login
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    session["next"] = next_url

    if not email or not password:
        return render_template("auth/login.html", next_url=next_url,
                               error="Introduce email y contraseña.")

    user = User.query.get(email)
    if not user or not user.password_hash or not check_password_hash(user.password_hash, password):
        return render_template("auth/login.html", next_url=next_url,
                               error="Email o contraseña incorrectos.")

    if not user.is_enabled:
        return render_template("auth/login.html", next_url=next_url,
                               error="Tu cuenta ha sido deshabilitada.")

    # Set session — same keys as Google OAuth callback
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()

    session["user_email"] = user.email
    session["user_name"] = user.name
    session["user_picture"] = user.picture
    session["is_admin"] = bool(user.is_admin)
    session.permanent = True

    return redirect(session.pop("next", "/"))


# ── Google OAuth redirect ─────────────────────────────────────────────────────

@auth_bp.route("/google")
def google_login():
    """Redirect to Google OAuth. Separate route so the login form can link here."""
    next_url = request.args.get("next", "/")
    session["next"] = next_url

    callback = os.environ.get(
        "GOOGLE_CALLBACK_URL",
        url_for("auth.callback", _external=True),
    )
    return _oauth().google.authorize_redirect(callback)


# ── Callback ──────────────────────────────────────────────────────────────────

@auth_bp.route("/callback")
def callback():
    token    = _oauth().google.authorize_access_token()
    userinfo = token.get("userinfo") or {}

    email   = userinfo.get("email")
    name    = userinfo.get("name")
    picture = userinfo.get("picture")

    if not email:
        return redirect("/")

    provider = "google"

    # Upsert user — create on first login, update fields on every login
    user = User.query.get(email)
    now  = datetime.now(timezone.utc)
    if user is None:
        user = User(
            email=email,
            name=name,
            picture=picture,
            provider=provider,
            created_at=now,
            last_login=now,
        )
        db.session.add(user)
        from app.mailer import send_new_user_notification
        send_new_user_notification({
            "email":      email,
            "name":       name,
            "provider":   provider,
            "created_at": now,
        })
    else:
        user.name       = name
        user.picture    = picture
        user.provider   = provider
        user.last_login = now

    db.session.commit()

    session["user_email"]   = email
    session["user_name"]    = name
    session["user_picture"] = picture
    session["is_admin"]     = bool(user.is_admin)
    session.permanent = True

    # Associate anonymous downloads made in this browser session before login
    anon_identity = session.pop("anon_identity_hash", None)
    if anon_identity:
        from app.models import Download
        updated = (
            Download.query
            .filter_by(user_email=None, identity_hash=anon_identity)
            .update({"user_email": email}, synchronize_session=False)
        )
        if updated:
            db.session.commit()

    return redirect(session.pop("next", "/"))


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    session.clear()
    return_to = os.environ.get("SITE_URL", "https://yt2mp3.f1madrid.win")
    return redirect(return_to)


# ── Me ────────────────────────────────────────────────────────────────────────

@auth_bp.route("/me")
def me():
    email = session.get("user_email")
    if email:
        # Resolve is_admin: prefer session cache, fall back to DB lookup
        is_admin = session.get("is_admin")
        if is_admin is None:
            user = User.query.get(email)
            is_admin = bool(user.is_admin) if user else False
            session["is_admin"] = is_admin
        return jsonify({
            "email":    email,
            "name":     session.get("user_name"),
            "picture":  session.get("user_picture"),
            "is_admin": is_admin,
        })
    return jsonify({"user": None}), 200
