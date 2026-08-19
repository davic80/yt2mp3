"""Tests for the geolocation database resolution order.

The bundled DB-IP database must live outside /app/geoip: docker-compose mounts
./geoip over that path, and an empty host directory hides anything the image
shipped there — which is exactly why geolocation was silently disabled.
"""
import os

import pytest

from app import geo


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    operator = tmp_path / "GeoLite2-City.mmdb"
    bundled = tmp_path / "bundled" / "dbip-city-lite.mmdb"
    bundled.parent.mkdir()
    monkeypatch.setenv("GEOIP_PATH", str(operator))
    monkeypatch.setenv("BUNDLED_GEOIP_PATH", str(bundled))
    return operator, bundled


def test_operator_database_wins(dbs):
    operator, bundled = dbs
    operator.write_bytes(b"maxmind")
    bundled.write_bytes(b"dbip")

    assert geo._resolve_db_path() == str(operator)


def test_falls_back_to_the_bundled_database(dbs):
    operator, bundled = dbs
    bundled.write_bytes(b"dbip")
    assert not operator.exists()

    assert geo._resolve_db_path() == str(bundled)


def test_none_when_neither_exists(dbs):
    assert geo._resolve_db_path() is None


def test_geolocate_is_silent_without_a_database(dbs, monkeypatch):
    monkeypatch.setattr(geo, "_reader", None)
    monkeypatch.setattr(geo, "_reader_attempted", False)

    assert geo.geolocate("8.8.8.8") == {"country_code": None, "city": None}


def test_bundled_path_is_outside_the_mounted_volume():
    # The regression that caused the outage: anything under /app/geoip is
    # shadowed by the ./geoip bind mount.
    assert not geo.BUNDLED_GEOIP_PATH.startswith("/app/geoip/")
