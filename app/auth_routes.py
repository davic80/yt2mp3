"""Auth0 OAuth blueprint — /auth/login, /auth/callback, /auth/logout, /auth/me"""
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, redirect, request, session, url_for

from app import db
from app.models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _oauth():
    """Lazy import so the OAuth object is only used after app init."""
    from app import oauth as _oauth_obj
    return _oauth_obj


# ── Login ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login():
    # Remember where to send the user after login
    next_url = request.args.get("next", "/")
    session["next"] = next_url

    callback = os.environ.get("AUTH0_CALLBACK_URL", url_for("auth.callback", _external=True))
    return _oauth().auth0.authorize_redirect(callback)


# ── Callback ──────────────────────────────────────────────────────────────────

@auth_bp.route("/callback")
def callback():
    token     = _oauth().auth0.authorize_access_token()
    userinfo  = token.get("userinfo") or {}

    email   = userinfo.get("email")
    name    = userinfo.get("name")
    picture = userinfo.get("picture")
    sub     = userinfo.get("sub", "")

    if not email:
        # Should not happen with openid+email scope, but handle gracefully
        return redirect("/")

    # Determine provider from Auth0 subject (e.g. "google-oauth2|…", "facebook|…")
    if sub.startswith("google"):
        provider = "google"
    elif sub.startswith("facebook"):
        provider = "facebook"
    else:
        provider = sub.split("|")[0] if "|" in sub else "auth0"

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
        # Notify admin of new registration — fire-and-forget, never blocks login
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

    # Store minimal user info in session
    session["user_email"]   = email
    session["user_name"]    = name
    session["user_picture"] = picture
    session.permanent = True

    # v3.1.0 — associate anonymous downloads made in this browser session
    # (identified by the session fingerprint stored before login) to this user.
    # We use the identity_hash stored on records that share the same browser
    # fingerprint from the pre-login session cookie.
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
    domain    = os.environ.get("AUTH0_DOMAIN", "")
    client_id = os.environ.get("AUTH0_CLIENT_ID", "")
    return_to = "https://diana.f1madrid.win"
    return redirect(
        f"https://{domain}/v2/logout"
        f"?returnTo={return_to}"
        f"&client_id={client_id}"
    )


# ── Me ────────────────────────────────────────────────────────────────────────

@auth_bp.route("/me")
def me():
    email = session.get("user_email")
    if email:
        return jsonify({
            "email":   email,
            "name":    session.get("user_name"),
            "picture": session.get("user_picture"),
        })
    return jsonify({"user": None}), 200
