"""
geo.py — IP geolocation from a MaxMind-format .mmdb database.

Usage:
    from app.geo import geolocate
    result = geolocate("1.2.3.4")
    # → {"country_code": "ES", "city": "Madrid"}
    # → {"country_code": None, "city": None}   on any failure

Two databases are consulted, in order:

1. ``GEOIP_PATH`` (default ``/app/geoip/GeoLite2-City.mmdb``) — the volume the
   operator can drop a MaxMind GeoLite2-City file into. MaxMind requires a free
   account plus a license key to download, and its licence forbids
   redistributing the file, so it cannot ship inside the image.
2. ``BUNDLED_GEOIP_PATH`` — DB-IP City Lite, baked into the image at build time.
   It is CC BY 4.0, needs no account, and is readable by the same geoip2
   reader, so it works out of the box with no operator setup.

Note the bundled copy deliberately lives *outside* /app/geoip: docker-compose
mounts ./geoip over that directory, and an empty host directory would hide
anything the image shipped there.

If neither file exists, or geoip2 is not installed, lookups return None
silently and the app runs without geolocation.
"""

import logging
import os

logger = logging.getLogger("app.geo")

BUNDLED_GEOIP_PATH = "/app/geoip-bundled/dbip-city-lite.mmdb"

_reader = None
_reader_attempted = False


def _resolve_db_path() -> str | None:
    """Return the first geolocation database that exists, or None."""
    operator_path = os.environ.get("GEOIP_PATH", "/app/geoip/GeoLite2-City.mmdb")
    bundled_path = os.environ.get("BUNDLED_GEOIP_PATH", BUNDLED_GEOIP_PATH)
    for path in (operator_path, bundled_path):
        if path and os.path.isfile(path):
            return path
    return None


def _get_reader():
    global _reader, _reader_attempted
    if _reader_attempted:
        return _reader
    _reader_attempted = True

    path = _resolve_db_path()
    if path is None:
        logger.info("geo: no geolocation database found — geolocation disabled")
        return None

    try:
        import geoip2.database  # type: ignore
        _reader = geoip2.database.Reader(path)
        logger.info(
            "geo: geolocation database loaded from %s (%s)",
            path, _reader.metadata().database_type,
        )
    except Exception as exc:
        logger.warning("geo: failed to load geolocation database: %s", exc)

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
