import pytest
from datetime import datetime
from decimal import Decimal

from edge_equation.ingestion.normalizer import normalize_slate
from edge_equation.ingestion.schema import Slate, GameInfo, MarketInfo


def _sample_games():
    return [{
        "league": "MLB", "game_id": "MLB-2026-04-20-DET-BOS",
        "start_time": "2026-04-20T13:05:00", "home_team": "BOS", "away_team": "DET",
        "meta": {"weather": "clear"}, "unknown_field": "should be ignored",
    }]


def _sample_markets():
    return [
        {"game_id": "MLB-2026-04-20-DET-BOS", "market_type": "ML", "selection": "BOS", "odds": -132},
        {"game_id": "MLB-2026-04-20-DET-BOS", "market_type": "Total", "selection": "Over",
         "line": "9.5", "odds": -110},
    ]


def test_normalize_produces_typed_slate():
    slate = normalize_slate(_sample_games(), _sample_markets())
    assert isinstance(slate, Slate)
    assert len(slate.games) == 1
    assert len(slate.markets) == 2
    g = slate.games[0]
    assert isinstance(g, GameInfo)
    assert g.sport == "MLB"
    assert g.league == "MLB"
    assert g.start_time == datetime(2026, 4, 20, 13, 5, 0)
    m0 = slate.markets[0]
    assert isinstance(m0, MarketInfo)
    assert m0.market_type == "ML"
    assert m0.odds == -132


def test_normalize_coerces_line_to_decimal():
    slate = normalize_slate(_sample_games(), _sample_markets())
    total = slate.markets[1]
    assert total.line == Decimal("9.5")
    assert isinstance(total.line, Decimal)


def test_normalize_accepts_datetime_object():
    games = [{"league": "MLB", "game_id": "g1",
              "start_time": datetime(2026, 4, 20, 13, 0, 0),
              "home_team": "BOS", "away_team": "DET"}]
    slate = normalize_slate(games, [])
    assert slate.games[0].start_time == datetime(2026, 4, 20, 13, 0, 0)


def test_normalize_ignores_unknown_fields():
    games = _sample_games()
    games[0]["weird_extra"] = "ignored"
    slate = normalize_slate(games, [])
    g = slate.games[0]
    assert not hasattr(g, "weird_extra")
    assert "weird_extra" not in g.meta


def test_normalize_missing_game_field_raises():
    games = [{"league": "MLB", "game_id": "g1", "start_time": "2026-04-20T13:00:00"}]
    with pytest.raises(ValueError, match="missing required fields"):
        normalize_slate(games, [])


def test_normalize_missing_market_field_raises():
    games = _sample_games()
    markets = [{"game_id": "MLB-2026-04-20-DET-BOS", "market_type": "ML"}]
    with pytest.raises(ValueError, match="missing required fields"):
        normalize_slate(games, markets)


def test_normalize_unknown_league_raises():
    games = [{"league": "CRICKET", "game_id": "g1", "start_time": "2026-04-20T13:00:00",
              "home_team": "A", "away_team": "B"}]
    with pytest.raises(ValueError, match="unknown league"):
        normalize_slate(games, [])


def test_normalize_market_type_invalid_for_league_raises():
    games = _sample_games()
    markets = [{"game_id": "MLB-2026-04-20-DET-BOS",
                "market_type": "Passing_Yards", "selection": "BOS"}]
    with pytest.raises(ValueError, match="not valid for league"):
        normalize_slate(games, markets)


def test_normalize_market_references_unknown_game_raises():
    games = _sample_games()
    markets = [{"game_id": "does-not-exist", "market_type": "ML", "selection": "BOS"}]
    with pytest.raises(ValueError, match="unknown game_id"):
        normalize_slate(games, markets)


def test_normalize_invalid_datetime_raises():
    games = [{"league": "MLB", "game_id": "g1", "start_time": "not-a-date",
              "home_team": "A", "away_team": "B"}]
    with pytest.raises(ValueError, match="Invalid start_time"):
        normalize_slate(games, [])
