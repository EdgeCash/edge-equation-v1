import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Pick, Line
from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.scoring import ConfidenceScorer


def _make_ml_bundle_det_at_bos():
    return FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )


def test_engine_ml_pick_matches_math_layer():
    bundle = _make_ml_bundle_det_at_bos()
    line = Line(odds=-132)
    pick = BettingEngine.evaluate(bundle, line, public_mode=False)

    fv = ProbabilityCalculator.calculate_fair_value(
        "ML", "MLB", bundle.inputs, bundle.universal_features
    )
    expected_fair_prob = fv["fair_prob"]
    expected_edge = EVCalculator.calculate_edge(expected_fair_prob, -132)
    dec_odds = EVCalculator.american_to_decimal(-132)
    expected_kelly_full = EVCalculator.kelly_fraction(expected_edge, dec_odds)
    expected_kelly_half = (expected_kelly_full / Decimal('2')).quantize(Decimal('0.0001'))
    expected_grade = ConfidenceScorer.grade(expected_edge)

    assert isinstance(pick, Pick)
    assert pick.fair_prob == expected_fair_prob
    assert pick.expected_value is None
    assert pick.edge == expected_edge
    if expected_edge >= Decimal('0.010000'):
        assert pick.kelly == expected_kelly_half
    else:
        assert pick.kelly == Decimal('0')
    assert pick.grade == expected_grade
    assert pick.realization == ConfidenceScorer.realization_for_grade(expected_grade)
    assert pick.sport == "MLB"
    assert pick.market_type == "ML"
    assert pick.selection == "BOS"
    assert pick.line.odds == -132
    assert pick.game_id == "MLB-2026-04-20-DET-BOS"


def test_engine_total_pick_returns_expected_value():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="Total",
        inputs={"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        universal_features={},
        selection="Over 9.5",
    )
    line = Line(odds=-110, number=Decimal('9.5'))
    pick = BettingEngine.evaluate(bundle, line)

    fv = ProbabilityCalculator.calculate_fair_value(
        "Total", "MLB", bundle.inputs, bundle.universal_features
    )
    assert pick.expected_value == fv["expected_total"]
    assert pick.fair_prob is None
    assert pick.edge is None
    assert pick.kelly is None


def test_engine_hr_prop_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="HR",
        inputs={"rate": 0.142},
        universal_features={"matchup_exploit": 0.08, "market_line_delta": 0.12},
        selection="Judge Over 0.5 HR",
    )
    line = Line(odds=+320, number=Decimal('0.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("HR", "MLB", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]
    assert pick.fair_prob is None


def test_engine_k_prop_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="K",
        inputs={"rate": 7.85},
        universal_features={"matchup_exploit": 0.09, "market_line_delta": 0.08},
        selection="Burnes Over 7.5 K",
    )
    line = Line(odds=-115, number=Decimal('7.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("K", "MLB", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_nfl_passing_yards_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="NFL",
        market_type="Passing_Yards",
        inputs={"rate": 312.4},
        universal_features={"form_off": 0.11, "matchup_strength": 0.09},
        selection="Mahomes Over 275.5",
    )
    line = Line(odds=-110, number=Decimal('275.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("Passing_Yards", "NFL", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_nhl_sog_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="NHL",
        market_type="SOG",
        inputs={"rate": 4.12},
        universal_features={"matchup_exploit": 0.10},
        selection="Crosby Over 4.5 SOG",
    )
    line = Line(odds=-115, number=Decimal('4.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("SOG", "NHL", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_public_mode_suppresses_edge_kelly():
    bundle = _make_ml_bundle_det_at_bos()
    line = Line(odds=-132)
    pick = BettingEngine.evaluate(bundle, line, public_mode=True)
    assert pick.fair_prob is not None
    assert pick.edge is None
    assert pick.kelly is None


def test_pick_is_frozen():
    bundle = _make_ml_bundle_det_at_bos()
    pick = BettingEngine.evaluate(bundle, Line(odds=-132))
    with pytest.raises(Exception):
        pick.edge = Decimal('0.5')
