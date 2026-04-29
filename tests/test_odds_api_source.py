from datetime import datetime
from decimal import Decimal
import pytest

from edge_equation.ingestion.odds_api_source import (
    TheOddsApiSource,
    ODDS_API_SPORT_MAP,
    MARKET_KEY_MAP,
)
from edge_equation.engines.core.data.odds_api_client import TheOddsApiClient
from edge_equation.persistence.db import Database
from edge_equation.persistence.odds_cache import OddsCache


NOW = datetime(2026, 4, 20, 12, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _sample_mlb_payload():
    return {
        "games": [
            {
                "id": "evt_1",
                "sport_key": "baseball_mlb",
                "commence_time": "2026-04-20T23:05:00Z",
                "home_team": "Boston Red Sox",
                "away_team": "Detroit Tigers",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Boston Red Sox", "price": -132},
                                    {"name": "Detroit Tigers", "price": 112},
                                ],
                            },
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over",  "price": -110, "point": 9.5},
                                    {"name": "Under", "price": -110, "point": 9.5},
                                ],
                            },
                        ],
                    },
                    {
                        "key": "fanduel",
                        "title": "FanDuel",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Boston Red Sox", "price": -135},
                                    {"name": "Detroit Tigers", "price": 115},
                                ],
                            },
                        ],
                    },
                ],
            }
        ]
    }


def _seed_cache(conn, sport_key, markets, payload):
    key = TheOddsApiClient.cache_key(sport_key, markets)
    OddsCache.put(conn, key, payload, ttl_seconds=900, now=NOW)


def test_league_from_sport_key_known():
    for api_key, league in ODDS_API_SPORT_MAP.items():
        assert TheOddsApiSource.league_from_sport_key(api_key) == league


def test_league_from_sport_key_soccer_prefix():
    assert TheOddsApiSource.league_from_sport_key("soccer_epl") == "SOC"
    assert TheOddsApiSource.league_from_sport_key("soccer_uefa_champs_league") == "SOC"


def test_league_from_sport_key_unknown_raises():
    with pytest.raises(ValueError, match="Unsupported sport_key"):
        TheOddsApiSource.league_from_sport_key("tiddlywinks")


def test_constructor_validates_sport_key(conn):
    with pytest.raises(ValueError, match="Unsupported sport_key"):
        TheOddsApiSource(conn, sport_key="badsport")


def test_market_key_map_coverage():
    assert MARKET_KEY_MAP["MLB"]["h2h"] == "ML"
    assert MARKET_KEY_MAP["MLB"]["spreads"] == "Run_Line"
    assert MARKET_KEY_MAP["NHL"]["spreads"] == "Puck_Line"
    assert MARKET_KEY_MAP["NFL"]["spreads"] == "Spread"


def test_get_raw_games_from_cached_payload(conn):
    _seed_cache(conn, "baseball_mlb", ["h2h", "totals"], _sample_mlb_payload())
    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h", "totals"])
    games = source.get_raw_games(now=NOW)
    assert len(games) == 1
    g = games[0]
    assert g["league"] == "MLB"
    assert g["game_id"] == "evt_1"
    assert g["home_team"] == "Boston Red Sox"
    assert g["away_team"] == "Detroit Tigers"


def test_get_raw_markets_picks_first_bookmaker_by_default(conn):
    _seed_cache(conn, "baseball_mlb", ["h2h", "totals"], _sample_mlb_payload())
    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h", "totals"])
    markets = source.get_raw_markets(now=NOW)
    # All markets should come from draftkings (first bookmaker)
    assert all(m["meta"]["bookmaker"] == "draftkings" for m in markets)


def test_get_raw_markets_respects_preferred_bookmaker(conn):
    _seed_cache(conn, "baseball_mlb", ["h2h", "totals"], _sample_mlb_payload())
    source = TheOddsApiSource(
        conn, sport_key="baseball_mlb", markets=["h2h", "totals"],
        preferred_bookmaker="fanduel",
    )
    markets = source.get_raw_markets(now=NOW)
    assert all(m["meta"]["bookmaker"] == "fanduel" for m in markets)
    h2h_home = next(m for m in markets if m["market_type"] == "ML" and "Boston" in m["selection"])
    assert h2h_home["odds"] == -135


def test_preferred_bookmaker_missing_falls_back_to_first(conn):
    _seed_cache(conn, "baseball_mlb", ["h2h", "totals"], _sample_mlb_payload())
    source = TheOddsApiSource(
        conn, sport_key="baseball_mlb", markets=["h2h", "totals"],
        preferred_bookmaker="pinnacle",
    )
    markets = source.get_raw_markets(now=NOW)
    assert all(m["meta"]["bookmaker"] == "draftkings" for m in markets)


def test_totals_selection_includes_point(conn):
    _seed_cache(conn, "baseball_mlb", ["h2h", "totals"], _sample_mlb_payload())
    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h", "totals"])
    markets = source.get_raw_markets(now=NOW)
    totals = [m for m in markets if m["market_type"] == "Total"]
    selections = {m["selection"] for m in totals}
    assert "Over 9.5" in selections
    assert "Under 9.5" in selections
    for m in totals:
        assert m["line"] == Decimal("9.5")


def test_ml_markets_have_no_line(conn):
    _seed_cache(conn, "baseball_mlb", ["h2h", "totals"], _sample_mlb_payload())
    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h", "totals"])
    markets = source.get_raw_markets(now=NOW)
    ml = [m for m in markets if m["market_type"] == "ML"]
    assert len(ml) == 2
    assert all(m["line"] is None for m in ml)


def test_spreads_selection_is_team_name_only(conn):
    """Post-fix: spreads selections carry just the team name so
    BettingEngine._resolve_selection_side can exact-match home/away.
    The point lives on MarketInfo.line. Embedding the point in the
    selection string (old behavior) silently made every spread pick
    ungradeable because "PIT -1.5" never equals "PIT"."""
    nhl_payload = {
        "games": [
            {
                "id": "nhl_1",
                "commence_time": "2026-04-20T23:00:00Z",
                "home_team": "Pittsburgh Penguins",
                "away_team": "Philadelphia Flyers",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Pittsburgh Penguins", "price": -120, "point": -1.5},
                                    {"name": "Philadelphia Flyers", "price": 110,  "point": 1.5},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    _seed_cache(conn, "icehockey_nhl", ["spreads"], nhl_payload)
    source = TheOddsApiSource(conn, sport_key="icehockey_nhl", markets=["spreads"])
    markets = source.get_raw_markets(now=NOW)
    assert len(markets) == 2
    assert all(m["market_type"] == "Puck_Line" for m in markets)
    selections = {m["selection"] for m in markets}
    # Team names only -- no embedded point.
    assert selections == {"Pittsburgh Penguins", "Philadelphia Flyers"}
    # Line is preserved per-outcome so downstream math still sees -1.5 / +1.5.
    lines_by_sel = {m["selection"]: m["line"] for m in markets}
    assert lines_by_sel["Pittsburgh Penguins"] == Decimal("-1.5")
    assert lines_by_sel["Philadelphia Flyers"] == Decimal("1.5")


def test_empty_bookmakers_yields_no_markets(conn):
    payload = {
        "games": [{
            "id": "e1",
            "commence_time": "2026-04-20T23:00:00Z",
            "home_team": "Home", "away_team": "Away",
            "bookmakers": [],
        }]
    }
    _seed_cache(conn, "baseball_mlb", ["h2h"], payload)
    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h"])
    assert source.get_raw_markets(now=NOW) == []
    # But the game itself is still reported
    games = source.get_raw_games(now=NOW)
    assert len(games) == 1


def test_unknown_api_market_key_is_skipped(conn):
    payload = {
        "games": [{
            "id": "e1",
            "commence_time": "2026-04-20T23:00:00Z",
            "home_team": "Home", "away_team": "Away",
            "bookmakers": [{
                "key": "dk",
                "markets": [
                    {"key": "player_home_runs", "outcomes": [{"name": "Judge", "price": 320, "point": 0.5}]},
                    {"key": "h2h", "outcomes": [{"name": "Home", "price": -110}, {"name": "Away", "price": -110}]},
                ],
            }],
        }]
    }
    _seed_cache(conn, "baseball_mlb", ["h2h"], payload)
    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h"])
    markets = source.get_raw_markets(now=NOW)
    # Only h2h survives the MARKET_KEY_MAP filter
    assert all(m["market_type"] == "ML" for m in markets)
    assert len(markets) == 2


def test_outcome_with_missing_price_is_skipped(conn):
    payload = {
        "games": [{
            "id": "e1",
            "commence_time": "2026-04-20T23:00:00Z",
            "home_team": "Home", "away_team": "Away",
            "bookmakers": [{
                "key": "dk",
                "markets": [{
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Home"},  # no price
                        {"name": "Away", "price": -110},
                    ],
                }],
            }],
        }]
    }
    _seed_cache(conn, "baseball_mlb", ["h2h"], payload)
    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h"])
    markets = source.get_raw_markets(now=NOW)
    assert len(markets) == 1
    assert markets[0]["selection"] == "Away"
