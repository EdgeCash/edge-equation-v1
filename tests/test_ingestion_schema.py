import pytest
from datetime import datetime
from decimal import Decimal

from edge_equation.ingestion.schema import GameInfo, MarketInfo, Slate


def test_game_info_construction_and_to_dict():
    g = GameInfo(
        sport="MLB", league="MLB", game_id="MLB-2026-04-20-DET-BOS",
        start_time=datetime(2026, 4, 20, 13, 5, 0),
        home_team="BOS", away_team="DET", meta={"weather": "clear"},
    )
    d = g.to_dict()
    assert d["sport"] == "MLB"
    assert d["league"] == "MLB"
    assert d["game_id"] == "MLB-2026-04-20-DET-BOS"
    assert d["start_time"] == "2026-04-20T13:05:00"
    assert d["home_team"] == "BOS"
    assert d["away_team"] == "DET"
    assert d["meta"] == {"weather": "clear"}


def test_game_info_is_frozen():
    g = GameInfo(sport="MLB", league="MLB", game_id="x",
                 start_time=datetime(2026, 1, 1), home_team="A", away_team="B")
    with pytest.raises(Exception):
        g.home_team = "Z"


def test_market_info_construction_and_to_dict():
    m = MarketInfo(game_id="MLB-2026-04-20-DET-BOS", market_type="Total",
                   selection="Over", line=Decimal("9.5"), odds=-110, meta={"source": "mock"})
    d = m.to_dict()
    assert d["game_id"] == "MLB-2026-04-20-DET-BOS"
    assert d["market_type"] == "Total"
    assert d["selection"] == "Over"
    assert d["line"] == "9.5"
    assert d["odds"] == -110
    assert d["meta"] == {"source": "mock"}


def test_market_info_with_no_line_and_no_odds():
    m = MarketInfo(game_id="x", market_type="ML", selection="BOS")
    d = m.to_dict()
    assert d["line"] is None
    assert d["odds"] is None


def test_market_info_is_frozen():
    m = MarketInfo(game_id="x", market_type="ML", selection="BOS")
    with pytest.raises(Exception):
        m.odds = 99


def test_slate_to_dict_and_from_lists():
    g = GameInfo(sport="MLB", league="MLB", game_id="g1",
                 start_time=datetime(2026, 4, 20, 13, 0, 0), home_team="BOS", away_team="DET")
    m = MarketInfo(game_id="g1", market_type="ML", selection="BOS", odds=-132)
    slate = Slate.from_lists([g], [m])
    d = slate.to_dict()
    assert len(d["games"]) == 1
    assert len(d["markets"]) == 1
    assert d["games"][0]["game_id"] == "g1"
    assert d["markets"][0]["selection"] == "BOS"
