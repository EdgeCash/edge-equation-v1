import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.posting.posting_formatter import (
    PostingFormatter,
    CARD_TEMPLATES,
    TAGLINE,
)


def _make_ml_pick():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )
    return BettingEngine.evaluate(bundle, Line(odds=-132))


def _make_total_pick():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="Total",
        inputs={"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        universal_features={},
        selection="Over 9.5",
    )
    return BettingEngine.evaluate(bundle, Line(odds=-110, number=Decimal('9.5')))


def test_daily_edge_structure():
    picks = [_make_ml_pick(), _make_total_pick()]
    card = PostingFormatter.build_card("daily_edge", picks)
    assert card["card_type"] == "daily_edge"
    assert card["headline"] == CARD_TEMPLATES["daily_edge"]["headline"]
    assert card["subhead"] == CARD_TEMPLATES["daily_edge"]["subhead"]
    assert card["tagline"] == TAGLINE
    assert card["tagline"] == "Facts. Not Feelings."
    assert len(card["picks"]) == 2
    assert "grade" in card["summary"]
    assert "edge" in card["summary"]
    assert "kelly" in card["summary"]


def test_pick_order_preserved():
    pick1 = _make_ml_pick()
    pick2 = _make_total_pick()
    card = PostingFormatter.build_card("daily_edge", [pick1, pick2])
    assert card["picks"][0]["market_type"] == "ML"
    assert card["picks"][1]["market_type"] == "Total"
    card2 = PostingFormatter.build_card("daily_edge", [pick2, pick1])
    assert card2["picks"][0]["market_type"] == "Total"
    assert card2["picks"][1]["market_type"] == "ML"


def test_summary_reports_best_grade_and_max_edge():
    ml_pick = _make_ml_pick()
    total_pick = _make_total_pick()
    card = PostingFormatter.build_card("daily_edge", [total_pick, ml_pick])
    assert card["summary"]["grade"] in ("A+", "A", "B", "C")
    assert card["summary"]["edge"] == str(ml_pick.edge)
    assert card["summary"]["kelly"] == str(ml_pick.kelly)


def test_all_card_types_buildable():
    pick = _make_ml_pick()
    for card_type in CARD_TEMPLATES.keys():
        card = PostingFormatter.build_card(card_type, [pick])
        assert card["card_type"] == card_type
        assert card["headline"] == CARD_TEMPLATES[card_type]["headline"]
        assert card["tagline"] == TAGLINE
        assert card["picks"][0]["selection"] == "BOS"


def test_unknown_card_type_raises():
    with pytest.raises(ValueError, match="Unknown card_type"):
        PostingFormatter.build_card("smash_of_the_day", [_make_ml_pick()])


def test_empty_picks_allowed():
    card = PostingFormatter.build_card("daily_edge", [])
    assert card["picks"] == []
    assert card["summary"]["grade"] == "C"
    assert card["summary"]["edge"] is None
    assert card["summary"]["kelly"] is None


def test_headline_override():
    pick = _make_ml_pick()
    card = PostingFormatter.build_card("model_highlight", [pick], headline_override="Custom Title")
    assert card["headline"] == "Custom Title"
    assert card["subhead"] == CARD_TEMPLATES["model_highlight"]["subhead"]
