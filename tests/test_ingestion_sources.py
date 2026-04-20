import pytest
from datetime import datetime

from edge_equation.ingestion.mlb_source import MlbLikeSource
from edge_equation.ingestion.nba_source import NbaSource
from edge_equation.ingestion.nhl_source import NhlSource
from edge_equation.ingestion.nfl_source import NflSource
from edge_equation.ingestion.soccer_source import SoccerSource
from edge_equation.ingestion.normalizer import normalize_slate, LEAGUE_MARKETS


RUN = datetime(2026, 4, 20, 9, 0, 0)


def _check_source(source, league):
    games = source.get_raw_games(RUN)
    markets = source.get_raw_markets(RUN)
    assert len(games) >= 2, f"{league}: expected >= 2 games"
    assert len(markets) >= 1, f"{league}: expected >= 1 market"
    game_ids = {g["game_id"] for g in games}
    for m in markets:
        assert m["game_id"] in game_ids, f"{league}: market references unknown game_id {m['game_id']}"
        assert m["market_type"] in LEAGUE_MARKETS[league], \
            f"{league}: market_type {m['market_type']} not valid for league"
    slate = normalize_slate(games, markets)
    assert len(slate.games) == len(games)
    assert len(slate.markets) == len(markets)


def test_mlb_source(): _check_source(MlbLikeSource("MLB"), "MLB")
def test_kbo_source(): _check_source(MlbLikeSource("KBO"), "KBO")
def test_npb_source(): _check_source(MlbLikeSource("NPB"), "NPB")
def test_nba_source(): _check_source(NbaSource(), "NBA")
def test_nhl_source(): _check_source(NhlSource(), "NHL")
def test_nfl_source(): _check_source(NflSource(), "NFL")
def test_soccer_source(): _check_source(SoccerSource(), "SOC")


def test_mlb_source_rejects_invalid_league():
    with pytest.raises(ValueError):
        MlbLikeSource("NBA")


def test_sources_are_deterministic():
    s1 = MlbLikeSource("MLB"); s2 = MlbLikeSource("MLB")
    assert s1.get_raw_games(RUN) == s2.get_raw_games(RUN)
    assert s1.get_raw_markets(RUN) == s2.get_raw_markets(RUN)
