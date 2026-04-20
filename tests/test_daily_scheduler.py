from datetime import datetime

from edge_equation.engine.daily_scheduler import (
    generate_daily_edge_card,
    generate_evening_edge_card,
)
from edge_equation.posting.posting_formatter import TAGLINE


def test_daily_edge_card_nonempty_and_well_formed():
    card = generate_daily_edge_card(datetime(2026, 4, 20, 9, 0, 0))
    assert card["card_type"] == "daily_edge"
    assert card["headline"]
    assert card["subhead"]
    assert card["tagline"] == TAGLINE
    assert len(card["picks"]) >= 1
    for p in card["picks"]:
        assert p["sport"]
        assert p["market_type"]
        assert "line" in p
        assert "grade" in p
    assert card["generated_at"] == "2026-04-20T09:00:00"


def test_evening_edge_card_nonempty_and_well_formed():
    card = generate_evening_edge_card(datetime(2026, 4, 20, 18, 0, 0))
    assert card["card_type"] == "evening_edge"
    assert len(card["picks"]) >= 1
    assert card["tagline"] == TAGLINE


def test_scheduler_public_mode_suppresses_edge():
    card = generate_daily_edge_card(datetime(2026, 4, 20, 9, 0, 0), public_mode=True)
    ml_picks = [p for p in card["picks"] if p["market_type"] == "ML"]
    assert ml_picks
    for p in ml_picks:
        assert p["edge"] is None
        assert p["kelly"] is None
