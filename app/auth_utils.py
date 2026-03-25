import re
import functools
from urllib.parse import urlsplit, urlencode, parse_qs
from flask import request, abort, session, redirect, url_for, make_response

# RFC-1918 + loopback ranges
_LOCAL_EXACT = {"127.0.0.1", "::1", "localhost"}
_LOCAL_RE = [
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^fd"),    # IPv6 ULA
    re.compile(r"^fe80"),  # IPv6 link-local
]


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


def local_only(f):
    """Decorator: reject with 403 if request is not from a local network address."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _is_local_request():
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _clean_next_url() -> str:
    """Return the current request path with 'fragment' query param stripped."""
    parts = urlsplit(request.full_path)
    qs = parse_qs(parts.query)
    qs.pop("fragment", None)
    return parts._replace(query=urlencode(qs, doseq=True)).geturl()


_DISABLED_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>yt2mp3 · cuenta deshabilitada</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet"/>
  <style>
    body {
      margin: 0; min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      background: #1c1c1c; color: #e0e0e0;
      font-family: 'Inter', system-ui, sans-serif;
    }
    .card {
      background: #222; border: 1px solid #333; border-radius: 8px;
      padding: 2.5rem 2rem; max-width: 420px; text-align: center;
    }
    .logo { font-size: 1.4rem; font-weight: 600; letter-spacing: -.5px; margin-bottom: 1.5rem; }
    .logo .sep { color: #27a008; }
    .logo .mp3 { color: #39FF14; }
    .msg { font-size: .95rem; line-height: 1.6; color: #bbb; }
    .msg strong { color: #e0e0e0; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">yt<span class="sep">2</span><span class="mp3">mp3</span></div>
    <div class="msg">
      <strong>Tu cuenta ha sido deshabilitada.</strong><br/>
      Contacta al administrador.
    </div>
  </div>
</body>
</html>"""


def user_required(f):
    """Local requests pass through unconditionally (admin sees everything).
    Remote requests require session['user_email'] — redirects to /auth/login?next=<path>.
    If the user exists but is_enabled=False, returns a 'disabled account' page."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if _is_local_request():
            return f(*args, **kwargs)
        email = session.get("user_email")
        if not email:
            return redirect(url_for("auth.login", next=_clean_next_url()))
        # Check if user is enabled
        from app.models import User
        user = User.query.get(email)
        if user and not user.is_enabled:
            return make_response(_DISABLED_HTML, 403)
        return f(*args, **kwargs)
    return decorated


def admin_or_local(f):
    """Allow local requests OR remote users with is_admin=True.
    Remote non-admin users get 403; unauthenticated remote users redirect to login."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if _is_local_request():
            return f(*args, **kwargs)
        email = session.get("user_email")
        if not email:
            return redirect(url_for("auth.login", next=_clean_next_url()))
        from app.models import User
        user = User.query.get(email)
        if not user or not user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def get_current_user_email() -> str | None:
    """Return the logged-in user's email from session, or None.

    For local (admin) requests this returns None intentionally — callers
    should apply no user filter when the return value is None AND the
    request is local.  Use _is_local_request() alongside this helper when
    you need to distinguish 'local no-filter' from 'anonymous remote'."""
    return session.get("user_email")
