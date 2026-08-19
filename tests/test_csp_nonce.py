"""The strict CSP: a per-request nonce instead of 'unsafe-inline'.

With 'unsafe-inline' an injected <script> runs whatever the policy says. With a
nonce it does not, because the attacker cannot guess the nonce. Measured in a
browser, injecting <img src=x onerror=...>:

    baseline CSP ('unsafe-inline')   the handler runs
    strict CSP   (nonce)             the handler is blocked

'strict-dynamic' is what lets this coexist with the SPA. Fragments arrive in
later requests, so a nonce minted for one of them would not match the policy
delivered with the document; 'strict-dynamic' instead trusts script inserted by
already-trusted script, and spa.js — which re-creates the fragment scripts — is
itself nonced.
"""
import re
from html.parser import HTMLParser

import pytest

PAGES = ["/", "/player/", "/mis-descargas/", "/settings/", "/db/", "/db/analytics"]
NONCE_IN_CSP = re.compile(r"'nonce-([A-Za-z0-9_-]+)'")


def _strict(resp):
    return resp.headers.get("Content-Security-Policy-Report-Only") \
        or resp.headers.get("Content-Security-Policy")


def test_default_mode_enforces_baseline_and_reports_strict(client):
    """The strict policy ships in observation first: nothing new can break
    while it is watched under real use, and promoting it is an env change."""
    resp = client.get("/")

    assert "'unsafe-inline'" in resp.headers["Content-Security-Policy"]
    strict = resp.headers["Content-Security-Policy-Report-Only"]
    assert "'unsafe-inline'" not in strict.split("style-src")[0]
    assert "'strict-dynamic'" in strict


def test_strict_mode_enforces_the_strict_policy(monkeypatch):
    monkeypatch.setenv("CSP_MODE", "strict")
    from app import create_app
    with create_app().test_client() as c:
        resp = c.get("/")
    csp = resp.headers["Content-Security-Policy"]
    assert "Content-Security-Policy-Report-Only" not in resp.headers
    assert NONCE_IN_CSP.search(csp)
    assert "'strict-dynamic'" in csp
    # The whole point: script-src must not fall back to allowing any inline.
    script_src = [d for d in csp.split("; ") if d.startswith("script-src")][0]
    assert "'unsafe-inline'" not in script_src


class _ScriptTags(HTMLParser):
    """Collect real <script> start tags.

    A regex is not good enough here: several fragments mention
    `<script src=...>` inside JavaScript comments, and the browser treats that
    as text, not markup. Only the parser knows the difference.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.tags.append(dict(attrs))


def _script_tags(html):
    parser = _ScriptTags()
    parser.feed(html)
    return parser.tags


@pytest.mark.parametrize("path", PAGES)
def test_every_script_tag_carries_the_request_nonce(client, path):
    """With 'strict-dynamic' a host allowlist is ignored, so the external
    module tags need the nonce just as much as the inline blocks do."""
    resp = client.get(path)
    nonce = NONCE_IN_CSP.search(_strict(resp)).group(1)

    tags = _script_tags(resp.get_data(as_text=True))
    assert tags, f"{path} rendered no script tags"

    unnonced = [t for t in tags if t.get("nonce") != nonce]
    assert not unnonced, (
        f"{path}: script tags without the request nonce — under the strict "
        f"policy these would not execute:\n"
        + "\n".join(str(t) for t in unnonced)
    )


def test_the_nonce_is_different_on_every_request(client):
    seen = set()
    for _ in range(5):
        resp = client.get("/")
        seen.add(NONCE_IN_CSP.search(_strict(resp)).group(1))
    assert len(seen) == 5, "the nonce is being reused — it is then guessable"


def test_the_nonce_is_long_enough_to_be_unguessable(client):
    resp = client.get("/")
    nonce = NONCE_IN_CSP.search(_strict(resp)).group(1)
    assert len(nonce) >= 16


def test_spa_propagates_the_nonce_when_it_re_creates_scripts():
    """Browsers hide the nonce content attribute once the document has a
    nonce-based CSP, so copying attributes alone yields an empty one; the IDL
    property is the one that carries the value."""
    with open("static/spa.js", encoding="utf-8") as fh:
        source = fh.read()
    assert "s.nonce = old.nonce" in source
