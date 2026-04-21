from datetime import datetime, timedelta
import json
import pytest
import httpx

from edge_equation.ingestion.odds_api_client import (
    TheOddsApiClient,
    API_KEY_ENV_VAR,
    DEFAULT_ENDPOINT,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.odds_cache import OddsCache


NOW = datetime(2026, 4, 20, 12, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _mock_client(payload_list, hit_counter):
    """Build an httpx.Client backed by MockTransport that serves payload_list
    for /v4/sports/... requests and counts each call in hit_counter."""
    def handler(request: httpx.Request) -> httpx.Response:
        hit_counter["count"] += 1
        hit_counter["last_url"] = str(request.url)
        hit_counter["last_params"] = dict(request.url.params)
        return httpx.Response(200, json=payload_list)
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url="https://api.the-odds-api.com")


def test_cache_key_deterministic_regardless_of_market_order():
    k1 = TheOddsApiClient.cache_key("baseball_mlb", ["h2h", "totals"])
    k2 = TheOddsApiClient.cache_key("baseball_mlb", ["totals", "h2h"])
    assert k1 == k2
    assert k1 == "theoddsapi:baseball_mlb:h2h,totals:us:american"


def test_cache_key_differentiates_regions_and_format():
    a = TheOddsApiClient.cache_key("baseball_mlb", ["h2h"], regions="us")
    b = TheOddsApiClient.cache_key("baseball_mlb", ["h2h"], regions="uk")
    c = TheOddsApiClient.cache_key("baseball_mlb", ["h2h"], odds_format="decimal")
    assert a != b
    assert a != c
    assert b != c


def test_fetch_returns_cached_payload_when_fresh(conn):
    cached = {"games": [{"id": "cached-event"}]}
    OddsCache.put(
        conn,
        TheOddsApiClient.cache_key("baseball_mlb", ["h2h"]),
        cached,
        ttl_seconds=900,
        now=NOW,
    )
    hit = {"count": 0}
    client = _mock_client([{"id": "fresh-event"}], hit)
    result = TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h"],
        api_key="TESTKEY", now=NOW, http_client=client,
    )
    assert result == cached
    assert hit["count"] == 0  # no HTTP call


def test_fetch_hits_api_on_cache_miss_and_writes_through(conn):
    hit = {"count": 0}
    payload_list = [{"id": "new-event", "home_team": "BOS", "away_team": "DET"}]
    client = _mock_client(payload_list, hit)

    result = TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h", "totals"],
        api_key="TESTKEY", now=NOW, http_client=client,
    )
    assert hit["count"] == 1
    assert result == {"games": payload_list}

    # Verify written through to cache
    stored = OddsCache.get(
        conn,
        TheOddsApiClient.cache_key("baseball_mlb", ["h2h", "totals"]),
        now=NOW,
    )
    assert stored == {"games": payload_list}


def test_fetch_reuses_cache_on_second_call(conn):
    hit = {"count": 0}
    client = _mock_client([{"id": "event-1"}], hit)
    TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h"],
        api_key="TESTKEY", now=NOW, http_client=client,
    )
    TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h"],
        api_key="TESTKEY", now=NOW, http_client=client,
    )
    assert hit["count"] == 1  # second call is cached


def test_fetch_expired_cache_refetches(conn):
    hit = {"count": 0}
    client = _mock_client([{"id": "event-1"}], hit)
    TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h"],
        api_key="TESTKEY", now=NOW, ttl_seconds=60, http_client=client,
    )
    later = NOW + timedelta(seconds=61)
    TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h"],
        api_key="TESTKEY", now=later, http_client=client,
    )
    assert hit["count"] == 2


def test_fetch_sends_api_key_and_params(conn):
    hit = {"count": 0}
    client = _mock_client([], hit)
    TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h", "totals"],
        regions="us,uk", odds_format="american",
        api_key="SECRET_KEY", now=NOW, http_client=client,
    )
    assert hit["last_params"]["apiKey"] == "SECRET_KEY"
    assert hit["last_params"]["regions"] == "us,uk"
    assert hit["last_params"]["markets"] == "h2h,totals"
    assert hit["last_params"]["oddsFormat"] == "american"
    assert hit["last_params"]["dateFormat"] == "iso"


def test_fetch_raises_on_missing_api_key(conn, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match="Odds API key"):
        TheOddsApiClient.fetch_odds(
            conn, sport_key="baseball_mlb", markets=["h2h"],
            now=NOW, http_client=_mock_client([], {"count": 0}),
        )


def test_fetch_uses_env_var_when_api_key_omitted(conn, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "ENV_KEY")
    hit = {"count": 0}
    client = _mock_client([], hit)
    TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h"],
        now=NOW, http_client=client,
    )
    assert hit["last_params"]["apiKey"] == "ENV_KEY"


def test_fetch_http_error_bubbles(conn):
    def handler(request):
        return httpx.Response(401, json={"message": "Invalid key"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        TheOddsApiClient.fetch_odds(
            conn, sport_key="baseball_mlb", markets=["h2h"],
            api_key="BAD", now=NOW, http_client=client,
        )


def test_clear_cache_all_theoddsapi_keys(conn):
    OddsCache.put(conn, "theoddsapi:a:h2h:us:american", {"v": 1}, ttl_seconds=900, now=NOW)
    OddsCache.put(conn, "theoddsapi:b:h2h:us:american", {"v": 2}, ttl_seconds=900, now=NOW)
    OddsCache.put(conn, "other:key", {"v": 3}, ttl_seconds=900, now=NOW)

    n = TheOddsApiClient.clear_cache(conn)
    assert n == 2
    assert OddsCache.get(conn, "other:key", now=NOW) == {"v": 3}


def test_clear_cache_scoped_to_sport(conn):
    OddsCache.put(conn, "theoddsapi:baseball_mlb:h2h:us:american", {"v": 1}, 900, NOW)
    OddsCache.put(conn, "theoddsapi:icehockey_nhl:h2h:us:american", {"v": 2}, 900, NOW)
    n = TheOddsApiClient.clear_cache(conn, sport_key="baseball_mlb")
    assert n == 1
    assert OddsCache.get(conn, "theoddsapi:icehockey_nhl:h2h:us:american", now=NOW) == {"v": 2}
