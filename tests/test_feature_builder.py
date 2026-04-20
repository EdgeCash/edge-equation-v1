import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder, FeatureBundle
from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.stats import DeterministicStats
from edge_equation.config.sport_config import SPORT_CONFIG


def test_build_valid_ml_bundle_det_at_bos():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085, "unknown_key_dropped": 999},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )
    assert isinstance(bundle, FeatureBundle)
    assert bundle.sport == "MLB"
    assert bundle.market_type == "ML"
    assert bundle.inputs["strength_home"] == 1.32
    assert "unknown_key_dropped" not in bundle.universal_features
    assert "home_edge" in bundle.universal_features
    assert bundle.selection == "BOS"


def test_bundle_feeds_math_layer_consistently_ml():
    inputs = {"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115}
    universal = {"home_edge": 0.085}
    bundle = FeatureBuilder.build(sport="MLB", market_type="ML", inputs=inputs, universal_features=universal)
    direct = ProbabilityCalculator.calculate_fair_value("ML", "MLB", inputs, universal)
    via_bundle = ProbabilityCalculator.calculate_fair_value(
        bundle.market_type, bundle.sport, bundle.inputs, bundle.universal_features
    )
    assert direct["fair_prob"] == via_bundle["fair_prob"]


def test_bundle_feeds_math_layer_consistently_total():
    inputs = {"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00}
    bundle = FeatureBuilder.build(sport="MLB", market_type="Total", inputs=inputs, universal_features={})
    direct = ProbabilityCalculator.calculate_fair_value("Total", "MLB", inputs, {})
    via_bundle = ProbabilityCalculator.calculate_fair_value(
        bundle.market_type, bundle.sport, bundle.inputs, bundle.universal_features
    )
    assert direct["expected_total"] == via_bundle["expected_total"]


def test_bundle_feeds_math_layer_consistently_prop_hr():
    inputs = {"rate": 0.142}
    universal = {"matchup_exploit": 0.08, "market_line_delta": 0.12}
    bundle = FeatureBuilder.build(sport="MLB", market_type="HR", inputs=inputs, universal_features=universal)
    direct = ProbabilityCalculator.calculate_fair_value("HR", "MLB", inputs, universal)
    via_bundle = ProbabilityCalculator.calculate_fair_value(
        bundle.market_type, bundle.sport, bundle.inputs, bundle.universal_features
    )
    assert direct["expected_value"] == via_bundle["expected_value"]


def test_invalid_sport_raises():
    with pytest.raises(ValueError, match="Unknown sport"):
        FeatureBuilder.build("NOT_A_SPORT", "ML", {"strength_home": 1.0, "strength_away": 1.0}, {})


def test_invalid_market_for_sport_raises():
    with pytest.raises(ValueError, match="not supported for sport"):
        FeatureBuilder.build("MLB", "Passing_Yards", {"rate": 250.0}, {})


def test_missing_required_inputs_raises():
    with pytest.raises(ValueError, match="Missing required inputs"):
        FeatureBuilder.build("MLB", "ML", {"strength_home": 1.0}, {})


def test_sport_weights_returns_config():
    weights = FeatureBuilder.sport_weights("MLB")
    assert weights["league_baseline_total"] == SPORT_CONFIG["MLB"]["league_baseline_total"]
    assert weights["ml_universal_weight"] == SPORT_CONFIG["MLB"]["ml_universal_weight"]
    assert weights["prop_universal_weight"] == SPORT_CONFIG["MLB"]["prop_universal_weight"]
