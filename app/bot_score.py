"""
bot_score.py — Heuristic bot detection scorer.

compute_bot_score(data) → int in range 0-100.
Signals used (no external IP-reputation DB required):
  +40  UA contains headless/automation keywords
  +30  ua_is_bot=True from the user-agents parser
  +20  fingerprint is absent / null (JS blocked)
  +20  WebGL renderer matches known headless defaults
  +10  combination: no referrer + suspicious UA + no fingerprint
"""

import json
import re

_HEADLESS_RE = re.compile(
    r"HeadlessChrome|PhantomJS|Selenium|Playwright|puppeteer|"
    r"webdriver|slimerjs|zombie|htmlunit|python-requests|python-urllib|"
    r"Go-http-client|curl/|wget/|scrapy",
    re.IGNORECASE,
)

# WebGL renderer values that headless Chrome / Chromium report by default
_HEADLESS_WEBGL = {
    "Google SwiftShader",
    "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), Google-0.0.0.0)",
    "",  # completely empty = JS blocked or very minimal browser
}


def _webgl_from_components(fp_components_json: str | None) -> str:
    if not fp_components_json:
        return ""
    try:
        comps = json.loads(fp_components_json)
        return (comps.get("webGL") or "").strip()
    except Exception:
        return ""


def compute_bot_score(
    ua_raw: str | None,
    ua_is_bot: bool,
    fingerprint_hash: str | None,
    fingerprint_components: str | None,
    referrer: str | None,
) -> int:
    score = 0
    ua = (ua_raw or "").strip()

    # +40 — headless / automation keywords in UA
    if _HEADLESS_RE.search(ua):
        score += 40

    # +30 — user-agents parser already flagged it
    if ua_is_bot:
        score += 30

    # +20 — no fingerprint at all (JS was blocked or not executed)
    if not fingerprint_hash:
        score += 20

    # +20 — WebGL renderer matches known headless defaults
    webgl = _webgl_from_components(fingerprint_components)
    if webgl in _HEADLESS_WEBGL:
        score += 20

    # +10 — combination: no referrer + suspicious UA + no fingerprint
    suspicious_ua = bool(_HEADLESS_RE.search(ua)) or ua_is_bot
    if not referrer and suspicious_ua and not fingerprint_hash:
        score += 10

    return min(score, 100)
