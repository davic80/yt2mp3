import re
import functools
from flask import request, abort

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
