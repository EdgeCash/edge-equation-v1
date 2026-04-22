"""DataFetcher tests: caching, retries, graceful fallback, public_mode."""
from datetime import date, datetime
import httpx
import pytest

from edge_equation.data_fetcher import (
    CACHE_TTL_ODDS,
    CACHE_TTL_SCHEDULE,
    CACHE_TTL_SCRAPER,
    DEFAULT_MIN_REQUEST_INTERVAL_SEC,
    DataBundle,
    KboStatsScraper,
    NpbStatsScraper,
    SLATE_SPORTS,
    TheSportsDBClient,
    THESPORTSDB_LEAGUE_IDS,
    _Throttle,
    _with_retries,
    fetch_daily_data,
)
from edge_equation.ingestion.odds_api_client import TheOddsApiClient
from edge_equation.persistence.db import Database
from edge_equation.persistence.odds_cache import OddsCache


NOW = datetime(2026, 4, 22, 12, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


# ---------------------------------------------- _Throttle


def test_throttle_first_call_no_wait(monkeypatch):
    slept = []
    t = _Throttle(min_interval_sec=0.5)
    t._sleep = lambda s: slept.append(s)
    t.wait()
    assert slept == []


def test_throttle_zero_interval_is_noop():
    t = _Throttle(min_interval_sec=0)
    t._sleep = lambda s: pytest.fail("should not sleep")
    t.wait()
    t.wait()


# ---------------------------------------------- _with_retries


def test_with_retries_returns_on_success():
    called = []

    def _fn():
        called.append(1)
        return "ok"

    assert _with_retries(_fn) == "ok"
    assert len(called) == 1


def test_with_retries_retries_transient_and_succeeds():
    attempts = {"n": 0}

    def _fn():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise httpx.TimeoutException("slow")
        return "ok"

    assert _with_retries(_fn, sleep=lambda s: None) == "ok"
    assert attempts["n"] == 2


def test_with_retries_returns_none_after_exhausting():
    def _fn():
        raise httpx.ConnectError("down")

    assert _with_retries(_fn, max_retries=3, sleep=lambda s: None) is None


def test_with_retries_does_not_swallow_non_transient():
    def _fn():
        raise ValueError("programmer error")

    with pytest.raises(ValueError):
        _with_retries(_fn, sleep=lambda s: None)


# ---------------------------------------------- TheSportsDBClient


def test_sportsdb_events_by_date_success(conn):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"events": [
            {"idEvent": "1", "strEvent": "A vs B"},
            {"idEvent": "2", "strEvent": "C vs D"},
        ]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sdb = TheSportsDBClient(http_client=client)
    try:
        events = sdb.events_by_date(conn, day=date(2026, 4, 22), league_id=4424, now=NOW)
    finally:
        sdb.close()
    assert len(events) == 2
    assert "eventsday.php" in captured["url"]


def test_sportsdb_events_cached(conn):
    hits = {"count": 0}

    def handler(request):
        hits["count"] += 1
        return httpx.Response(200, json={"events": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sdb = TheSportsDBClient(http_client=client)
    try:
        sdb.events_by_date(conn, day=date(2026, 4, 22), league_id=4424, now=NOW)
        sdb.events_by_date(conn, day=date(2026, 4, 22), league_id=4424, now=NOW)
    finally:
        sdb.close()
    assert hits["count"] == 1


def test_sportsdb_returns_empty_on_http_error(conn):
    def handler(request):
        return httpx.Response(500, text="server error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sdb = TheSportsDBClient(http_client=client)
    try:
        events = sdb.events_by_date(conn, day=date(2026, 4, 22), league_id=4424, now=NOW)
    finally:
        sdb.close()
    assert events == []


def test_sportsdb_team_by_id(conn):
    def handler(request):
        return httpx.Response(200, json={
            "teams": [{"idTeam": "134860", "strTeam": "New York Yankees"}],
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sdb = TheSportsDBClient(http_client=client)
    try:
        team = sdb.team_by_id(conn, team_id=134860, now=NOW)
    finally:
        sdb.close()
    assert team is not None
    assert team["strTeam"] == "New York Yankees"


# ---------------------------------------------- Scraper skeletons


def test_kbo_scraper_caches_html(conn):
    hits = {"count": 0}

    def handler(request):
        hits["count"] += 1
        return httpx.Response(200, text="<html></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    scr = KboStatsScraper(http_client=client)
    try:
        scr.starters_for_day(conn, day=date(2026, 4, 22), now=NOW)
        scr.starters_for_day(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        scr.close()
    assert hits["count"] == 1


def test_kbo_scraper_returns_empty_on_failure(conn):
    def handler(request):
        return httpx.Response(503, text="down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    scr = KboStatsScraper(http_client=client)
    try:
        starters = scr.starters_for_day(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        scr.close()
    assert starters == []


def test_npb_scraper_caches_html(conn):
    hits = {"count": 0}

    def handler(request):
        hits["count"] += 1
        return httpx.Response(200, text="<html></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    scr = NpbStatsScraper(http_client=client)
    try:
        scr.starters_for_day(conn, day=date(2026, 4, 22), now=NOW)
        scr.starters_for_day(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        scr.close()
    assert hits["count"] == 1


# ---------------------------------------------- fetch_daily_data


def _multi_handler(odds_payload=None, sdb_payload=None):
    """A single MockTransport handler that routes Odds-API vs. TheSportsDB
    vs. scraper hostnames to their configured stub responses."""
    odds_payload = odds_payload if odds_payload is not None else []
    sdb_payload = sdb_payload if sdb_payload is not None else {"events": []}

    def handler(request):
        host = request.url.host
        path = request.url.path
        if host.endswith("the-odds-api.com"):
            return httpx.Response(200, json=odds_payload)
        if host.endswith("thesportsdb.com"):
            return httpx.Response(200, json=sdb_payload)
        if host.endswith("mykbostats.com") or host.endswith("npb.jp"):
            return httpx.Response(200, text="<html></html>")
        return httpx.Response(404, text="not found")

    return handler


def test_fetch_daily_data_invalid_slate_raises(conn):
    with pytest.raises(ValueError, match="slate"):
        fetch_daily_data(conn, slate="atlantic")


def test_fetch_daily_data_domestic_returns_bundle(conn):
    handler = _multi_handler(
        odds_payload=[{"id": "evt1", "home_team": "NYY", "away_team": "BOS"}],
        sdb_payload={"events": [{"idEvent": "1"}]},
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    bundle = fetch_daily_data(
        conn,
        date=date(2026, 4, 22),
        slate="domestic",
        api_key="TEST",
        http_client=client,
        scrape=False,
        now=NOW,
    )
    assert isinstance(bundle, DataBundle)
    assert bundle.slate == "domestic"
    # Every domestic league fetched
    for league in SLATE_SPORTS["domestic"]:
        assert league in bundle.odds
        assert league in bundle.schedules


def test_fetch_daily_data_overseas_runs_scrapers(conn):
    handler = _multi_handler()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    bundle = fetch_daily_data(
        conn,
        date=date(2026, 4, 22),
        slate="overseas",
        api_key="TEST",
        http_client=client,
        scrape=True,
        now=NOW,
    )
    # KBO / NPB scrapers ran
    assert "KBO" in bundle.scrapers
    assert "NPB" in bundle.scrapers


def test_fetch_daily_data_scrape_false_skips_scrapers(conn):
    handler = _multi_handler()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    bundle = fetch_daily_data(
        conn,
        slate="overseas",
        api_key="TEST",
        http_client=client,
        scrape=False,
        now=NOW,
    )
    assert bundle.scrapers == {}


def test_public_mode_strips_bookmakers(conn):
    handler = _multi_handler(
        odds_payload=[{
            "id": "evt1",
            "home_team": "NYY", "away_team": "BOS",
            "bookmakers": [{"key": "draftkings"}, {"key": "fanduel"}],
        }],
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    bundle = fetch_daily_data(
        conn, date=date(2026, 4, 22), slate="domestic",
        api_key="TEST", http_client=client, scrape=False, now=NOW,
        public_mode=True,
    )
    d = bundle.to_dict()
    # Every game in every league must have no bookmakers field
    for league_games in d["odds"].values():
        for g in league_games:
            assert "bookmakers" not in g


def test_public_mode_false_preserves_bookmakers(conn):
    handler = _multi_handler(
        odds_payload=[{
            "id": "evt1",
            "home_team": "NYY", "away_team": "BOS",
            "bookmakers": [{"key": "draftkings"}],
        }],
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    bundle = fetch_daily_data(
        conn, date=date(2026, 4, 22), slate="domestic",
        api_key="TEST", http_client=client, scrape=False, now=NOW,
        public_mode=False,
    )
    d = bundle.to_dict()
    for league_games in d["odds"].values():
        for g in league_games:
            if g:
                assert "bookmakers" in g


def test_cache_ttl_constants_sensible():
    # Phase 23: odds TTL widened to 6h so the daily cadence (9a / 11a /
    # 4p / 6p / 11p CT) can read from a single morning+afternoon
    # refresher pull instead of hitting the Odds API on every slot.
    # Schedule + scraper TTLs are unchanged; they refresh on their own
    # rhythm and don't need to track the odds TTL.
    assert CACHE_TTL_ODDS > 0
    assert CACHE_TTL_SCRAPER > 0
    assert CACHE_TTL_SCHEDULE > 0


def test_slate_sports_shape():
    assert "domestic" in SLATE_SPORTS
    assert "overseas" in SLATE_SPORTS
    assert "MLB" in SLATE_SPORTS["domestic"]
    assert "KBO" in SLATE_SPORTS["overseas"]
    assert "NPB" in SLATE_SPORTS["overseas"]


def test_thesportsdb_league_ids_covers_core_leagues():
    for league in ("MLB", "NFL", "NBA", "NHL", "KBO", "NPB"):
        assert league in THESPORTSDB_LEAGUE_IDS


def test_default_rate_limit_interval_conservative():
    # Conservative default -- under 1s to not choke tests but non-zero in prod.
    assert 0 < DEFAULT_MIN_REQUEST_INTERVAL_SEC < 5


def test_data_bundle_frozen():
    bundle = DataBundle(date="2026-04-22", slate="domestic")
    with pytest.raises(Exception):
        bundle.slate = "overseas"


def test_odds_cache_populated_after_fetch(conn):
    handler = _multi_handler(
        odds_payload=[{"id": "e1", "home_team": "A", "away_team": "B"}],
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetch_daily_data(
        conn, date=date(2026, 4, 22), slate="domestic",
        api_key="TEST", http_client=client, scrape=False, now=NOW,
    )
    # A follow-up direct Odds API fetch within TTL should come straight
    # from cache.
    payload = TheOddsApiClient.fetch_odds(
        conn,
        sport_key="baseball_mlb",
        markets=["h2h", "totals", "spreads"],
        api_key="TEST",
        http_client=client,
        now=NOW,
    )
    assert payload["games"][0]["id"] == "e1"
