import json
from flask import request
from user_agents import parse as ua_parse


def _cf_ip(req) -> str:
    """Prefer Cloudflare's real IP header, fallback to X-Forwarded-For, then remote_addr."""
    return (
        req.headers.get("CF-Connecting-IP")
        or req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or req.remote_addr
        or ""
    )


def collect(client_fingerprint: str | None = None) -> dict:
    """
    Collect visitor metadata from the current request context.
    Returns a flat dict ready to be applied to a Download model instance.
    No cookie data is collected.
    """
    ua_string = request.headers.get("User-Agent", "")
    ua = ua_parse(ua_string)

    device_type = "Mobile" if ua.is_mobile else ("Tablet" if ua.is_tablet else "PC")

    # Fingerprint components (JSON string from client-side script)
    fp_components = None
    fingerprint_hash = None
    if client_fingerprint:
        try:
            parsed = json.loads(client_fingerprint)
            fp_components = json.dumps(parsed.get("components"))
            fingerprint_hash = parsed.get("visitorId") or parsed.get("hash")
        except Exception:
            fingerprint_hash = client_fingerprint

    return {
        "ip_address": _cf_ip(request),
        "referrer": request.referrer,
        "user_agent_raw": ua_string,
        "ua_browser": ua.browser.family,
        "ua_browser_version": ua.browser.version_string,
        "ua_os": ua.os.family,
        "ua_os_version": ua.os.version_string,
        "ua_device": device_type,
        "ua_is_mobile": ua.is_mobile,
        "ua_is_bot": ua.is_bot,
        "accept_language": request.headers.get("Accept-Language", ""),
        "fingerprint_hash": fingerprint_hash,
        "fingerprint_components": fp_components,
    }
