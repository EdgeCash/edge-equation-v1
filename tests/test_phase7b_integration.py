"""
Phase 7b end-to-end integration:
- FeatureBuilder accepts a context_bundle kwarg
- ContextRegistry.compose merges into home_adv and dixon_coles_adj
- Composed adjustment is stored in metadata for audit
- Existing behavior unchanged when context_bundle is None
- Backtest primitives (walk-forward, bankroll, calibration, grading) compose
  in a plausible evaluate-fold workflow
"""
from datetime import date
from decimal import Decimal

import pytest

from edge_equation.engine.feature_builder import (
    FeatureBuilder,
    META_CONTEXT_ADJUSTMENT_KEY,
)
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.context.registry import ContextBundle, ContextRegistry
from edge_equation.context.rest import RestContext
from edge_equation.context.travel import TravelContext
from edge_equation.context.weather import WeatherContext
from edge_equation.context.situational import SituationalContext
from edge_equation.context.injuries import InjuriesContext
from edge_equation.backtest.walk_forward import WalkForward
from edge_equation.backtest.bankroll import BankrollSimulator, BetOutcome
from edge_equation.backtest.calibration import Calibration
from edge_equation.backtest.grading import GradeCalibrator


def test_backcompat_no_context_bundle_no_metadata_key():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.3, "strength_away": 1.1, "home_adv": 0.115},
        universal_features={},
        selection="BOS",
    )
    assert META_CONTEXT_ADJUSTMENT_KEY not in bundle.metadata


def test_context_bundle_adds_home_adv_delta():
    cb = ContextBundle(
        rest=RestContext(sport="NBA", home_rest_days=3, away_rest_days=0),
    )
    expected_delta = ContextRegistry.compose(cb).home_adv_delta

    bundle = FeatureBuilder.build(
        sport="NCAA_Basketball",
        market_type="ML",
        inputs={"strength_home": 1.3, "strength_away": 1.1, "home_adv": 0.10},
        universal_features={},
        selection="HOME",
        context_bundle=cb,
    )
    expected = float(Decimal('0.10') + expected_delta)
    assert bundle.inputs["home_adv"] == pytest.approx(expected, rel=1e-9)


def test_context_bundle_adds_totals_delta_to_dixon_coles_adj():
    cb = ContextBundle(
        weather=WeatherContext(sport="MLB", temperature_f=30.0, wind_mph=25.0),
    )
    composed = ContextRegistry.compose(cb)

    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="Total",
        inputs={"off_env": 1.0, "def_env": 1.0, "pace": 1.0, "dixon_coles_adj": 0.0},
        universal_features={},
        selection="Over 8.5",
        context_bundle=cb,
    )
    expected = float(composed.totals_delta)
    assert bundle.inputs["dixon_coles_adj"] == pytest.approx(expected, rel=1e-9)


def test_context_bundle_stored_in_metadata():
    cb = ContextBundle(
        injuries=InjuriesContext(
            sport="NBA",
            home_injury_impact=Decimal('0.1'),
            away_injury_impact=Decimal('0.4'),
        ),
    )
    bundle = FeatureBuilder.build(
        sport="NCAA_Basketball",
        market_type="ML",
        inputs={"strength_home": 1.3, "strength_away": 1.1, "home_adv": 0.0},
        universal_features={},
        selection="HOME",
        context_bundle=cb,
    )
    assert META_CONTEXT_ADJUSTMENT_KEY in bundle.metadata
    stored = bundle.metadata[META_CONTEXT_ADJUSTMENT_KEY]
    assert "components" in stored
    assert "injuries" in stored["components"]


def test_context_bundle_no_home_adv_in_inputs_initializes_from_delta():
    # When inputs has no home_adv key, context should inject home_adv_delta as the value.
    cb = ContextBundle(
        situational=SituationalContext(sport="NBA", home_b2b=True),
    )
    expected = float(ContextRegistry.compose(cb).home_adv_delta)
    bundle = FeatureBuilder.build(
        sport="NCAA_Basketball",
        market_type="ML",
        inputs={"strength_home": 1.2, "strength_away": 1.1},
        universal_features={},
        selection="HOME",
        context_bundle=cb,
    )
    assert bundle.inputs["home_adv"] == pytest.approx(expected, rel=1e-9)


def test_context_bundle_builds_all_the_way_to_pick():
    cb = ContextBundle(
        rest=RestContext(sport="NBA", home_rest_days=2, away_rest_days=0),
        travel=TravelContext(sport="NBA", away_travel_miles=1800.0, timezone_change_hours=3),
    )
    bundle = FeatureBuilder.build(
        sport="NCAA_Basketball",
        market_type="ML",
        inputs={"strength_home": 1.3, "strength_away": 1.1, "home_adv": 0.0},
        universal_features={},
        selection="HOME",
        context_bundle=cb,
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-120))
    assert pick.fair_prob is not None
    # Metadata propagates through the engine (via bundle.metadata spread)
    assert META_CONTEXT_ADJUSTMENT_KEY in pick.metadata


def test_walk_forward_fold_plus_bankroll_end_to_end():
    # Deterministic mini-backtest: 3 folds, fake bets in test windows.
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=date(2026, 2, 28),
        first_train_days=20,
        test_days=7,
        step_days=7,
    )
    assert len(folds) >= 3

    # Simulate a small book of bets from the first test window.
    bets = [
        BetOutcome(stake=Decimal('25'), payout=Decimal('0')),
        BetOutcome(stake=Decimal('25'), payout=Decimal('47.50')),
        BetOutcome(stake=Decimal('25'), payout=Decimal('50')),
    ]
    m = BankrollSimulator.simulate(Decimal('1000'), bets)
    assert m.n_bets == 3
    assert m.final_bankroll == Decimal('1000') - Decimal('25') + Decimal('22.50') + Decimal('25')


def test_calibration_feeds_grading_end_to_end():
    # Deterministic predictions and outcomes -> calibration.brier computed,
    # then a historical edge distribution is graded by GradeCalibrator.
    preds = [0.55, 0.62, 0.48, 0.70, 0.35]
    outcomes = [1, 1, 0, 1, 0]
    cal = Calibration.compute(preds, outcomes, n_bins=5)
    assert cal.brier >= Decimal('0')
    assert cal.n == 5

    edges = [e / 1000.0 for e in range(-20, 80)]  # -0.020 to 0.079
    thresholds = GradeCalibrator.fit(edges)
    assert GradeCalibrator.grade(Decimal('0.06'), thresholds) in ("A", "B")
    assert GradeCalibrator.grade(Decimal('-0.01'), thresholds) in ("D", "F")


def test_phase7a_and_7b_kwargs_compose_cleanly():
    # decay + hfa + context_bundle all at once; builder should not raise.
    from edge_equation.math.decay import DecayWeights
    decay = DecayWeights.for_sport("NFL")
    cb = ContextBundle(
        rest=RestContext(sport="NFL", home_rest_days=7, away_rest_days=4),
        weather=WeatherContext(sport="NFL", temperature_f=25.0, wind_mph=18.0),
    )
    bundle = FeatureBuilder.build(
        sport="NFL",
        market_type="Total",
        inputs={
            "off_env": 1.0,
            "def_env": 1.0,
            "pace": 1.0,
            "dixon_coles_adj": 0.0,
        },
        universal_features={},
        selection="Under 45.5",
        decay_params=decay,
        hfa_context={"home_team": "SEA", "venue": "DOME"},
        context_bundle=cb,
    )
    # All three metadata keys should be present.
    from edge_equation.engine.feature_builder import (
        META_DECAY_HALFLIFE_KEY,
        META_HFA_VALUE_KEY,
    )
    assert META_DECAY_HALFLIFE_KEY in bundle.metadata
    assert META_HFA_VALUE_KEY in bundle.metadata
    assert META_CONTEXT_ADJUSTMENT_KEY in bundle.metadata
