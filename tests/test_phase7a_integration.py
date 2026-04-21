"""
Phase 7a end-to-end integration:
- Builder accepts decay_params and hfa_context
- Decay-weighted strengths flow through to the math layer
- Dynamic HFA overrides home_adv
- Pick surfaces decay_halflife_days, hfa_value, kelly_breakdown
- Existing behavior unchanged when new kwargs are absent
"""
from decimal import Decimal

import pytest

from edge_equation.engine.feature_builder import (
    FeatureBuilder,
    META_DECAY_HALFLIFE_KEY,
    META_HFA_VALUE_KEY,
)
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Pick, Line
from edge_equation.math.decay import DecayWeights
from edge_equation.math.hfa import HFACalculator
from edge_equation.math.kelly_adaptive import AdaptiveKelly, KellyInputs


def test_backcompat_no_new_kwargs_pick_has_null_phase7a_fields():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-132))
    assert pick.decay_halflife_days is None
    assert pick.hfa_value is None
    assert pick.kelly_breakdown is None
    d = pick.to_dict()
    assert d["decay_halflife_days"] is None
    assert d["hfa_value"] is None
    assert d["kelly_breakdown"] is None


def test_decay_params_replaces_strength_with_weighted_mean():
    decay = DecayWeights.for_sport("MLB")
    # Home recent strong (1.4 @ 5d), older weaker (1.0 @ 200d)
    home_hist = [(1.4, 5.0), (1.0, 200.0)]
    away_hist = [(1.1, 10.0), (1.15, 150.0)]

    expected_home = DecayWeights.weighted_mean(
        [1.4, 1.0], [5.0, 200.0], decay.xi
    )
    expected_away = DecayWeights.weighted_mean(
        [1.1, 1.15], [10.0, 150.0], decay.xi
    )

    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={
            "home_strength_history": home_hist,
            "away_strength_history": away_hist,
            "home_adv": 0.115,
        },
        universal_features={},
        selection="BOS",
        decay_params=decay,
    )
    assert bundle.inputs["strength_home"] == float(expected_home)
    assert bundle.inputs["strength_away"] == float(expected_away)
    # History lists should be consumed (not left on the bundle)
    assert "home_strength_history" not in bundle.inputs
    assert "away_strength_history" not in bundle.inputs
    # Halflife recorded in metadata
    assert bundle.metadata[META_DECAY_HALFLIFE_KEY] == str(decay.halflife_days())


def test_hfa_context_overrides_home_adv_and_propagates_to_pick():
    bundle = FeatureBuilder.build(
        sport="NFL",
        market_type="ML",
        inputs={"strength_home": 1.2, "strength_away": 1.1, "home_adv": 0.0},
        universal_features={},
        selection="DEN",
        hfa_context={"home_team": "DEN"},
    )
    expected_hfa = HFACalculator.get_home_adv("NFL", team="DEN")
    assert bundle.inputs["home_adv"] == float(expected_hfa.total)
    assert bundle.metadata[META_HFA_VALUE_KEY] == str(expected_hfa.total)

    pick = BettingEngine.evaluate(bundle, Line(odds=-120))
    assert pick.hfa_value == expected_hfa.total


def test_hfa_context_with_venue_stacks_bonus():
    bundle = FeatureBuilder.build(
        sport="NFL",
        market_type="ML",
        inputs={"strength_home": 1.2, "strength_away": 1.1},
        universal_features={},
        selection="DEN",
        hfa_context={"home_team": "DEN", "venue": "DOME"},
    )
    # DEN override 0.50 + DOME bonus 0.50 = 1.00
    assert bundle.inputs["home_adv"] == 1.0


def test_hfa_context_requires_home_team():
    with pytest.raises(ValueError, match="home_team"):
        FeatureBuilder.build(
            sport="NFL",
            market_type="ML",
            inputs={"strength_home": 1.2, "strength_away": 1.1},
            universal_features={},
            hfa_context={"venue": "DOME"},
        )


def test_decay_and_hfa_both_applied_e2e():
    decay = DecayWeights.for_sport("NFL")
    bundle = FeatureBuilder.build(
        sport="NFL",
        market_type="ML",
        inputs={
            "home_strength_history": [(1.5, 3.0), (1.2, 100.0)],
            "away_strength_history": [(1.0, 3.0), (1.1, 100.0)],
        },
        universal_features={"home_edge": 0.05},
        selection="DEN",
        decay_params=decay,
        hfa_context={"home_team": "DEN", "venue": "DOME"},
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-110))
    # Both new Pick fields populated
    assert pick.decay_halflife_days == decay.halflife_days()
    assert pick.hfa_value == Decimal('1.000000')
    # Dict round-trips
    d = pick.to_dict()
    assert d["decay_halflife_days"] == str(decay.halflife_days())
    assert d["hfa_value"] == "1.000000"


def test_pick_kelly_breakdown_roundtrips_through_to_dict():
    # kelly_breakdown is an arbitrary dict that can be produced by AdaptiveKelly
    # and attached to a Pick via the standard constructor.
    inp = KellyInputs(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        sample_size=100,
        portfolio_size=1,
    )
    result = AdaptiveKelly.compute(inp)
    pick = Pick(
        sport="MLB",
        market_type="ML",
        selection="BOS",
        line=Line(odds=-132),
        kelly_breakdown=result.to_dict(),
    )
    d = pick.to_dict()
    assert d["kelly_breakdown"]["kelly_final"] == str(result.kelly_final)
    assert d["kelly_breakdown"]["capped"] is False


def test_decay_only_no_history_keeps_explicit_strengths():
    # If caller passes decay_params but no history, explicit strengths stand.
    decay = DecayWeights.for_sport("NBA")
    bundle = FeatureBuilder.build(
        sport="NBA" if False else "MLB",  # MLB supports ML
        market_type="ML",
        inputs={"strength_home": 1.5, "strength_away": 1.2},
        universal_features={},
        selection="BOS",
        decay_params=decay,
    )
    assert bundle.inputs["strength_home"] == 1.5
    assert bundle.inputs["strength_away"] == 1.2
    # Halflife still recorded
    assert bundle.metadata[META_DECAY_HALFLIFE_KEY] == str(decay.halflife_days())


def test_pick_with_phase7a_fields_is_frozen():
    pick = Pick(
        sport="MLB",
        market_type="ML",
        selection="BOS",
        line=Line(odds=-132),
        decay_halflife_days=Decimal('277.258872'),
        hfa_value=Decimal('0.500000'),
    )
    with pytest.raises(Exception):
        pick.hfa_value = Decimal('9.99')
