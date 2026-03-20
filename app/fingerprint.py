import json
import re
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


def _parse_cookies(cookie_header: str | None) -> dict:
    """Parse a raw cookie string into a dict (server-side, only non-HttpOnly cookies)."""
    if not cookie_header:
        return {}
    cookies = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def collect(client_fingerprint: str | None = None, client_cookies: str | None = None) -> dict:
    """
    Collect all available metadata from the current request context.
    Returns a flat dict ready to be applied to a Download model instance.
    """
    ua_string = request.headers.get("User-Agent", "")
    ua = ua_parse(ua_string)

    device_type = "Mobile" if ua.is_mobile else ("Tablet" if ua.is_tablet else "PC")

    # Server-side cookies (non-HttpOnly)
    server_cookies = dict(request.cookies)

    # Client-side cookies sent from JS (may overlap or add more)
    client_cookie_dict = {}
    if client_cookies:
        try:
            client_cookie_dict = json.loads(client_cookies)
        except Exception:
            pass

    all_cookies = {**server_cookies, **client_cookie_dict}
    cookies_json = json.dumps(all_cookies) if all_cookies else None

    # Tracking cookies
    fb_fbp = all_cookies.get("_fbp")
    fb_fbc = all_cookies.get("_fbc")
    ig_did = all_cookies.get("ig_did")
    ga_client = all_cookies.get("_ga")
    ga_session = next(
        (v for k, v in all_cookies.items() if re.match(r"_ga_[A-Z0-9]+", k)), None
    )

    # Fingerprint components (JSON string from FingerprintJS)
    fp_components = None
    if client_fingerprint:
        try:
            parsed = json.loads(client_fingerprint)
            fp_components = json.dumps(parsed.get("components"))
            fingerprint_hash = parsed.get("visitorId") or parsed.get("hash")
        except Exception:
            fingerprint_hash = client_fingerprint
    else:
        fingerprint_hash = None

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
        "cookies_json": cookies_json,
        "fb_fbp": fb_fbp,
        "fb_fbc": fb_fbc,
        "ga_client": ga_client,
        "ga_session": ga_session,
        "ig_did": ig_did,
    }
