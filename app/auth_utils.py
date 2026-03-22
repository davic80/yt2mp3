import re
import functools
from flask import request, abort, session, redirect, url_for

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


def user_required(f):
    """Local requests pass through unconditionally (admin sees everything).
    Remote requests require session['user_email'] — redirects to /auth/login?next=<path>."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if _is_local_request():
            return f(*args, **kwargs)
        if not session.get("user_email"):
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def get_current_user_email() -> str | None:
    """Return the logged-in user's email from session, or None.

    For local (admin) requests this returns None intentionally — callers
    should apply no user filter when the return value is None AND the
    request is local.  Use _is_local_request() alongside this helper when
    you need to distinguish 'local no-filter' from 'anonymous remote'."""
    return session.get("user_email")
