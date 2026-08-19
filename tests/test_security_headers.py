"""Security headers, and the one header the service worker always needed.

The CSP is built from an audit of what the front end actually loads, not from
a template: Chart.js on jsdelivr for the analytics page, Google Fonts, and
cover art from whatever host iTunes/Deezer/Genius/YouTube hands back.
"""
import pytest


PAGES = ["/", "/player/", "/mis-descargas/", "/settings/",
         "/db/", "/db/users", "/db/analytics", "/auth/login"]


@pytest.mark.parametrize("path", PAGES)
def test_baseline_headers_on_every_page(client, path):
    resp = client.get(path)

    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in resp.headers


@pytest.mark.parametrize("directive", [
    # Everything the audit found the front end genuinely needs.
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",   # Chart.js
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https:",                                  # cover art
    "media-src 'self' blob:",                                       # /player/stream
    "connect-src 'self'",
    # …and the parts that do the actual blocking.
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'self'",
])
def test_csp_contains_directive(client, directive):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert directive in csp


def test_service_worker_may_claim_the_root_scope(client):
    """cache-manager.js registers /static/sw.js with scope '/'. Without this
    header the browser caps it at /static/ and registration fails, which is
    why the offline audio cache never worked."""
    resp = client.get("/static/sw.js")

    assert resp.status_code == 200
    assert resp.headers.get("Service-Worker-Allowed") == "/"


def test_the_scope_header_is_not_sprayed_everywhere(client):
    assert "Service-Worker-Allowed" not in client.get("/").headers


def test_csp_can_be_downgraded_without_a_code_change(monkeypatch):
    """An escape hatch: CSP_MODE=report-only or off, no redeploy of code."""
    monkeypatch.setenv("CSP_MODE", "report-only")
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        resp = c.get("/")
    assert "Content-Security-Policy" not in resp.headers
    assert "Content-Security-Policy-Report-Only" in resp.headers


def test_csp_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("CSP_MODE", "off")
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        resp = c.get("/")
    assert "Content-Security-Policy" not in resp.headers
    assert "Content-Security-Policy-Report-Only" not in resp.headers
    # The cheap headers stay regardless — they have no failure mode.
    assert resp.headers["X-Frame-Options"] == "DENY"
