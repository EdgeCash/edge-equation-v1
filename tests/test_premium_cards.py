from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.premium.premium_pick import PremiumPick
from edge_equation.premium.premium_cards import (
    build_premium_daily_edge_card,
    build_premium_overseas_edge_card,
)


def _make_premium_picks():
    bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS", selection="BOS",
    )
    pick1 = BettingEngine.evaluate(bundle, Line(odds=-132))
    pp1 = PremiumPick(
        base_pick=pick1,
        p10=Decimal("0.580000"), p50=Decimal("0.620000"),
        p90=Decimal("0.655000"), mean=Decimal("0.618000"),
        notes="High-confidence ML.",
    )

    bundle2 = FeatureBuilder.build(
        sport="MLB", market_type="Total",
        inputs={"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        universal_features={},
        selection="Over 9.5",
    )
    pick2 = BettingEngine.evaluate(bundle2, Line(odds=-110, number=Decimal("9.5")))
    pp2 = PremiumPick(
        base_pick=pick2,
        p10=Decimal("9.50"), p50=Decimal("11.52"), p90=Decimal("13.50"),
        mean=Decimal("11.52"),
        notes="MC total with 15% stdev assumption.",
    )
    return [pp1, pp2]


def test_premium_daily_edge_card_structure():
    card = build_premium_daily_edge_card(_make_premium_picks())
    assert card["card_type"] == "premium_daily_edge"
    assert card["headline"] == "Premium Daily Edge"
    assert card["subhead"] == "Full distributions and model notes."
    assert card["tagline"] == "Facts. Not Feelings."
    assert len(card["picks"]) == 2
    # Each pick must have the distribution fields
    for p in card["picks"]:
        for k in ("p10", "p50", "p90", "mean", "notes"):
            assert k in p


def test_premium_overseas_edge_card_structure():
    card = build_premium_overseas_edge_card(_make_premium_picks())
    assert card["card_type"] == "premium_overseas_edge"
    assert card["headline"] == "Premium Overseas Edge"
    assert card["tagline"] == "Facts. Not Feelings."
    assert len(card["picks"]) == 2


def test_premium_cards_preserve_order():
    picks = _make_premium_picks()
    card = build_premium_daily_edge_card(picks)
    assert card["picks"][0]["market_type"] == "ML"
    assert card["picks"][1]["market_type"] == "Total"
    reversed_card = build_premium_daily_edge_card(list(reversed(picks)))
    assert reversed_card["picks"][0]["market_type"] == "Total"
    assert reversed_card["picks"][1]["market_type"] == "ML"


def test_premium_cards_empty_picks():
    card = build_premium_daily_edge_card([])
    assert card["picks"] == []
    assert card["tagline"] == "Facts. Not Feelings."
