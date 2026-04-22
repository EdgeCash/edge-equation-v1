import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.premium.premium_pick import PremiumPick
from edge_equation.premium.premium_formatter import format_premium_pick


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


def _make_premium_ml_pick():
    pick = _make_ml_pick()
    return PremiumPick(
        base_pick=pick,
        p10=Decimal("0.580000"),
        p50=Decimal("0.620000"),
        p90=Decimal("0.655000"),
        mean=Decimal("0.618000"),
        notes="Deterministic MC with 1000 iterations.",
    )


def test_premium_pick_wraps_base_pick():
    pp = _make_premium_ml_pick()
    assert pp.base_pick.fair_prob is not None
    assert pp.base_pick.selection == "BOS"
    assert pp.p10 == Decimal("0.580000")


def test_premium_pick_is_frozen():
    pp = _make_premium_ml_pick()
    with pytest.raises(Exception):
        pp.p50 = Decimal("0.9")


def test_premium_pick_to_dict():
    pp = _make_premium_ml_pick()
    d = pp.to_dict()
    assert d["base_pick"]["selection"] == "BOS"
    assert d["p10"] == "0.580000"
    assert d["p50"] == "0.620000"
    assert d["p90"] == "0.655000"
    assert d["mean"] == "0.618000"
    assert d["notes"] == "Deterministic MC with 1000 iterations."


def test_premium_pick_minimal_no_quantiles():
    pick = _make_ml_pick()
    pp = PremiumPick(base_pick=pick)
    d = pp.to_dict()
    assert d["p10"] is None
    assert d["p50"] is None
    assert d["p90"] is None
    assert d["mean"] is None
    assert d["notes"] is None


def test_format_premium_pick_returns_expected_keys():
    pp = _make_premium_ml_pick()
    out = format_premium_pick(pp)
    expected_keys = {
        "selection", "market_type", "sport", "line",
        "fair_prob", "expected_value", "edge", "grade", "kelly",
        "p10", "p50", "p90", "mean", "notes",
        "game_id", "event_time",
    }
    assert set(out.keys()) == expected_keys
    assert out["selection"] == "BOS"
    assert out["market_type"] == "ML"
    # Phase 18 tightened grade thresholds: this fixture's edge
    # (0.049167) now lands in the B tier.
    assert out["grade"] == "B"
    assert out["p50"] == "0.620000"
    assert out["notes"] == "Deterministic MC with 1000 iterations."


def test_format_premium_pick_values_match_base_pick():
    pp = _make_premium_ml_pick()
    out = format_premium_pick(pp)
    base = pp.base_pick
    assert out["fair_prob"] == str(base.fair_prob)
    assert out["edge"] == str(base.edge)
    assert out["kelly"] == str(base.kelly)
    assert out["game_id"] == base.game_id
