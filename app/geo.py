"""
geo.py — IP geolocation via MaxMind GeoLite2-City (.mmdb).

Usage:
    from app.geo import geolocate
    result = geolocate("1.2.3.4")
    # → {"country_code": "ES", "city": "Madrid"}
    # → {"country_code": None, "city": None}   on any failure

The .mmdb path is read from the GEOIP_PATH env var (default /app/geoip/GeoLite2-City.mmdb).
If the file does not exist or geoip2 is not installed, all lookups return None silently.
"""

import logging
import os

logger = logging.getLogger("app.geo")

_reader = None
_reader_attempted = False


def _get_reader():
    global _reader, _reader_attempted
    if _reader_attempted:
        return _reader
    _reader_attempted = True

    path = os.environ.get("GEOIP_PATH", "/app/geoip/GeoLite2-City.mmdb")
    if not os.path.isfile(path):
        logger.info("geo: GeoLite2 database not found at %s — geolocation disabled", path)
        return None

    try:
        import geoip2.database  # type: ignore
        _reader = geoip2.database.Reader(path)
        logger.info("geo: GeoLite2 database loaded from %s", path)
    except Exception as exc:
        logger.warning("geo: failed to load GeoLite2 database: %s", exc)

    return _reader


def geolocate(ip: str | None) -> dict:
    """
    Returns {"country_code": str|None, "city": str|None}.
    Never raises; returns None values on any error.
    """
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return {"country_code": None, "city": None}

    reader = _get_reader()
    if reader is None:
        return {"country_code": None, "city": None}

    try:
        resp = reader.city(ip)
        country_code = resp.country.iso_code or None
        city = resp.city.name or None
        return {"country_code": country_code, "city": city}
    except Exception:
        return {"country_code": None, "city": None}
