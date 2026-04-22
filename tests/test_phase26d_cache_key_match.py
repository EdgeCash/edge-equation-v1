"""
Phase 26d -- refresher and cadence must share the same OddsCache keys.

The cache key is `theoddsapi:{sport_key}:{regions}:{odds_format}:
{','.join(sorted(markets))}`. Any difference in markets between the
writer (DataFetcher -> TheOddsApiClient.fetch_odds) and the reader
(TheOddsApiSource -> TheOddsApiClient.fetch_odds) produces a silent
cache miss -- cadence workflows with cached_only=True then emit an
empty slate, and the premium email arrives with 0 picks.

This module locks that invariant so it can't drift again.
"""
from edge_equation.data_fetcher import (
    DEFAULT_TTL_SECONDS,
    ODDS_API_SPORT_KEY,
)
from edge_equation.ingestion.odds_api_client import TheOddsApiClient
from edge_equation.ingestion.odds_api_source import TheOddsApiSource


# Keep this constant in lockstep with data_fetcher._league_odds.
_REFRESHER_DEFAULT_MARKETS = ["h2h", "totals", "spreads"]


def test_odds_api_source_default_markets_include_spreads():
    """TheOddsApiSource is the cadence reader. Its default markets must
    include 'spreads' so its computed cache_key matches what the
    refresher wrote."""
    src = TheOddsApiSource(conn=None, sport_key="baseball_mlb")
    assert "spreads" in src.markets
    assert set(src.markets) == set(_REFRESHER_DEFAULT_MARKETS)


def test_refresher_and_cadence_produce_same_cache_key():
    """Direct cache_key comparison -- the specific string both sides
    compute must be identical. If this assert fails, cadence reads
    miss the refresher's writes and every email is empty."""
    refresher_key = TheOddsApiClient.cache_key(
        sport_key="baseball_mlb",
        markets=_REFRESHER_DEFAULT_MARKETS,
        regions="us",
        odds_format="american",
    )
    cadence_src = TheOddsApiSource(conn=None, sport_key="baseball_mlb")
    cadence_key = TheOddsApiClient.cache_key(
        sport_key="baseball_mlb",
        markets=cadence_src.markets,
        regions=cadence_src.regions,
        odds_format="american",
    )
    assert refresher_key == cadence_key


def test_cache_key_is_stable_across_every_league_sport_key():
    """Guard against a future sport key sneaking in with a different
    markets default."""
    for sport_key in sorted(ODDS_API_SPORT_KEY.values()):
        src = TheOddsApiSource(conn=None, sport_key=sport_key)
        refresher_key = TheOddsApiClient.cache_key(
            sport_key=sport_key,
            markets=_REFRESHER_DEFAULT_MARKETS,
            regions="us",
            odds_format="american",
        )
        cadence_key = TheOddsApiClient.cache_key(
            sport_key=sport_key,
            markets=src.markets,
            regions="us",
            odds_format="american",
        )
        assert refresher_key == cadence_key, (
            f"sport_key {sport_key!r} produces mismatched cache keys: "
            f"refresher={refresher_key!r} cadence={cadence_key!r}"
        )
