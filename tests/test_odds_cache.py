from datetime import datetime, timedelta
import pytest

from edge_equation.persistence.db import Database
from edge_equation.persistence.odds_cache import OddsCache


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


NOW = datetime(2026, 4, 20, 12, 0, 0)


def test_get_missing_key_returns_none(conn):
    assert OddsCache.get(conn, "missing", now=NOW) is None


def test_put_then_get_returns_payload(conn):
    payload = {"sport": "MLB", "games": [{"id": 1, "odds": -132}]}
    OddsCache.put(conn, "mlb:h2h", payload, ttl_seconds=900, now=NOW)
    fetched = OddsCache.get(conn, "mlb:h2h", now=NOW)
    assert fetched == payload


def test_get_returns_none_after_expiry(conn):
    OddsCache.put(conn, "mlb:h2h", {"x": 1}, ttl_seconds=60, now=NOW)
    later = NOW + timedelta(seconds=61)
    assert OddsCache.get(conn, "mlb:h2h", now=later) is None


def test_get_fresh_just_before_expiry(conn):
    OddsCache.put(conn, "mlb:h2h", {"x": 1}, ttl_seconds=60, now=NOW)
    just_before = NOW + timedelta(seconds=59)
    assert OddsCache.get(conn, "mlb:h2h", now=just_before) == {"x": 1}


def test_put_upserts_existing_key(conn):
    OddsCache.put(conn, "mlb:h2h", {"v": 1}, ttl_seconds=60, now=NOW)
    OddsCache.put(conn, "mlb:h2h", {"v": 2}, ttl_seconds=60, now=NOW)
    fetched = OddsCache.get(conn, "mlb:h2h", now=NOW)
    assert fetched == {"v": 2}


def test_put_negative_ttl_raises(conn):
    with pytest.raises(ValueError, match="ttl_seconds"):
        OddsCache.put(conn, "k", {}, ttl_seconds=0, now=NOW)
    with pytest.raises(ValueError, match="ttl_seconds"):
        OddsCache.put(conn, "k", {}, ttl_seconds=-5, now=NOW)


def test_purge_expired_removes_stale_rows(conn):
    OddsCache.put(conn, "fresh", {"v": 1}, ttl_seconds=600, now=NOW)
    OddsCache.put(conn, "stale", {"v": 2}, ttl_seconds=10, now=NOW)
    later = NOW + timedelta(seconds=3600)
    n = OddsCache.purge_expired(conn, now=later)
    assert n == 2
    assert OddsCache.get(conn, "fresh", now=later) is None
    assert OddsCache.get(conn, "stale", now=later) is None


def test_purge_expired_spares_fresh_rows(conn):
    OddsCache.put(conn, "fresh", {"v": 1}, ttl_seconds=600, now=NOW)
    OddsCache.put(conn, "stale", {"v": 2}, ttl_seconds=10, now=NOW)
    middle = NOW + timedelta(seconds=60)
    n = OddsCache.purge_expired(conn, now=middle)
    assert n == 1
    assert OddsCache.get(conn, "fresh", now=middle) == {"v": 1}
    assert OddsCache.get(conn, "stale", now=middle) is None


def test_stats_counts_fresh_and_expired(conn):
    OddsCache.put(conn, "fresh", {"v": 1}, ttl_seconds=600, now=NOW)
    OddsCache.put(conn, "stale", {"v": 2}, ttl_seconds=10, now=NOW)
    later = NOW + timedelta(seconds=60)
    stats = OddsCache.stats(conn, now=later)
    assert stats == {"total": 2, "fresh": 1, "expired": 1}


def test_nested_payload_roundtrip(conn):
    payload = {
        "meta": {"fetched": "2026-04-20T12:00:00Z", "provider": "TheOddsAPI"},
        "games": [
            {"id": "G1", "home": "BOS", "away": "DET", "markets": [{"type": "h2h", "odds": [-132, 112]}]},
            {"id": "G2", "home": "NYY", "away": "TB",  "markets": [{"type": "totals", "line": 8.5}]},
        ],
    }
    OddsCache.put(conn, "mlb:full", payload, ttl_seconds=600, now=NOW)
    fetched = OddsCache.get(conn, "mlb:full", now=NOW)
    assert fetched == payload
