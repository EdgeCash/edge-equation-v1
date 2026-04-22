"""
Cached-only credit guardrails and disclaimer-position invariant.

Cached-only:
  - TheOddsApiClient.fetch_odds with cached_only=True must NEVER call
    the network on a cache miss; it returns {"games": []} instead.
  - DataFetcher.fetch_daily_data with cached_only=True propagates the
    flag to every sub-client so NO live request leaves the runner.

Disclaimer invariant:
  - In any public_mode X render, DISCLAIMER_TEXT must appear exactly
    once and the 1-800-GAMBLER phone number must appear exactly once.
    The ledger footer + disclaimer belong at the BOTTOM of the text
    chain, not after each pick.
"""
import sqlite3
from decimal import Decimal

import httpx
import pytest

from edge_equation.compliance.disclaimer import DISCLAIMER_TEXT
from edge_equation.data_fetcher import (
    KboStatsScraper,
    TheSportsDBClient,
    fetch_daily_data,
)
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.ingestion.odds_api_client import TheOddsApiClient
from edge_equation.persistence.db import Database
from edge_equation.posting.ledger import LedgerStats
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.publishing.x_formatter import format_card


# ------------------------------------------------ fixtures

@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _exploding_client():
    """An httpx.Client whose handler raises if the fetcher tries to use
    it. If the guardrail is working, cached_only=True never calls us."""
    def handler(request):
        raise AssertionError(
            f"cached_only=True made a network call: {request.method} {request.url}"
        )
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------ TheOddsApiClient

def test_fetch_odds_cached_only_returns_empty_on_miss(conn):
    client = _exploding_client()
    payload = TheOddsApiClient.fetch_odds(
        conn,
        sport_key="baseball_mlb",
        markets=["h2h"],
        http_client=client,
        cached_only=True,
    )
    assert payload == {"games": []}


def test_fetch_odds_cached_only_serves_cache_when_present(conn):
    # Prime the cache through a normal call (the mock returns some data).
    def handler(request):
        return httpx.Response(200, json=[{"id": "game-1"}])
    client = httpx.Client(transport=httpx.MockTransport(handler))
    TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h"],
        http_client=client, api_key="k",
    )
    # Now cached_only=True should return that cached payload without
    # touching the network.
    exploder = _exploding_client()
    payload = TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h"],
        http_client=exploder, cached_only=True,
    )
    assert payload["games"] == [{"id": "game-1"}]


# ------------------------------------------------ DataFetcher fan-out

def test_fetch_daily_data_cached_only_no_network(conn, monkeypatch):
    # Make every outbound HTTP call raise.
    exploder = _exploding_client()
    bundle = fetch_daily_data(
        conn, slate="domestic", cached_only=True,
        http_client=exploder, scrape=False,
    )
    # Every league is present in odds/schedules with an empty list.
    assert set(bundle.odds.keys()) == {"MLB", "NFL", "NBA", "NHL"}
    for league, games in bundle.odds.items():
        assert games == []
    for league, events in bundle.schedules.items():
        assert events == []


def test_sportsdb_events_cached_only_no_network(conn):
    exploder = _exploding_client()
    sdb = TheSportsDBClient(http_client=exploder)
    from datetime import date
    events = sdb.events_by_date(
        conn, day=date(2026, 4, 22), league_id=4424, cached_only=True,
    )
    assert events == []


def test_kbo_scraper_cached_only_no_network(conn):
    exploder = _exploding_client()
    scr = KboStatsScraper(http_client=exploder)
    from datetime import date
    games = scr.starters_for_day(
        conn, date(2026, 4, 22), cached_only=True,
    )
    assert games == []


def test_cache_ttl_is_six_hours():
    """The cadence slots span ~14 hours; a 6h TTL means the refresher
    runs must cover the day (8am + 3pm CT is sufficient)."""
    from edge_equation.data_fetcher import CACHE_TTL_ODDS
    assert CACHE_TTL_ODDS == 6 * 60 * 60


# ------------------------------------------------ disclaimer invariant

def _public_card_with(picks):
    return PostingFormatter.build_card(
        card_type="daily_edge", picks=picks,
        public_mode=True,
        ledger_stats=LedgerStats(
            wins=0, losses=0, pushes=0,
            units_net=Decimal("0"), roi_pct=Decimal("0.0"),
            total_plays=0,
        ),
        skip_filter=True,
    )


def _pick(grade="A"):
    return Pick(
        sport="MLB", market_type="ML", selection="Home",
        line=Line(odds=-110),
        fair_prob=Decimal("0.55"),
        edge=Decimal("0.06"), kelly=Decimal("0.02"),
        grade=grade, game_id="G1",
        metadata={"home_team": "NYY", "away_team": "BOS"},
    )


def test_disclaimer_appears_exactly_once_in_public_render():
    card = _public_card_with([_pick(), _pick()])
    text = format_card(card)
    assert text.count(DISCLAIMER_TEXT) == 1


def test_gambler_hotline_appears_exactly_once_in_public_render():
    card = _public_card_with([_pick(), _pick(), _pick()])
    text = format_card(card)
    # 1-800-GAMBLER is the canonical number; it belongs in the ledger
    # footer and NOWHERE else.
    assert text.count("1-800-GAMBLER") == 1
    assert text.count("Bet within your means") == 1


def test_disclaimer_and_footer_sit_at_bottom_of_chain():
    card = _public_card_with([_pick()])
    text = format_card(card)
    disclaimer_pos = text.index(DISCLAIMER_TEXT)
    gambler_pos = text.index("1-800-GAMBLER")
    first_pick_pos = text.index("BOS @ NYY")
    # Both appear AFTER every pick block.
    assert disclaimer_pos > first_pick_pos
    assert gambler_pos > first_pick_pos


def test_single_pick_card_still_has_exactly_one_footer_chain():
    card = _public_card_with([_pick()])
    text = format_card(card)
    assert text.count(DISCLAIMER_TEXT) == 1
    assert text.count("1-800-GAMBLER") == 1
