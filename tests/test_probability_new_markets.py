"""Regression tests for the market branches added in the spread/NRFI PR.

Covers the directional/structural properties that matter for correctness
(signs, complement relationships, home/away mirroring). Exact calibration
magnitudes are deliberately NOT asserted because the `line / (baseline*0.5)`
formula is a known follow-up calibration item.
"""
from decimal import Decimal

import pytest

from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.pick_schema import Line
from edge_equation.math.probability import ProbabilityCalculator


# ---------------------------------------------------------------------------
# Spread / Run_Line / Puck_Line -- directional line adjustment
# ---------------------------------------------------------------------------

def _bt_inputs(line=None):
    inputs = {
        "strength_home": 1.0,
        "strength_away": 1.0,
        "home_adv": 0.115,
    }
    if line is not None:
        inputs["line"] = line
    return inputs


def test_puck_line_home_favorite_is_below_ml():
    """Home -1.5 should grade LOWER than ML on the same game: covering a
    negative line is harder than winning outright."""
    ml = ProbabilityCalculator.calculate_fair_value(
        "ML", "NHL", _bt_inputs(), {},
    )
    puck = ProbabilityCalculator.calculate_fair_value(
        "Puck_Line", "NHL", _bt_inputs(line=-1.5), {},
    )
    assert puck["fair_prob"] < ml["fair_prob"]


def test_puck_line_home_dog_is_above_ml():
    """Home +1.5 should grade HIGHER than ML: home only has to lose by 1 or
    win outright to cover."""
    ml = ProbabilityCalculator.calculate_fair_value(
        "ML", "NHL", _bt_inputs(), {},
    )
    puck = ProbabilityCalculator.calculate_fair_value(
        "Puck_Line", "NHL", _bt_inputs(line=1.5), {},
    )
    assert puck["fair_prob"] > ml["fair_prob"]


def test_spread_zero_line_matches_ml_direction():
    """A 0-point spread reduces to ML directionally: fair_prob should equal
    the ML result for the same inputs (both use BT + universal adjustment
    with a zero line adjustment)."""
    ml = ProbabilityCalculator.calculate_fair_value(
        "ML", "NFL", _bt_inputs(), {},
    )
    spread = ProbabilityCalculator.calculate_fair_value(
        "Spread", "NFL", _bt_inputs(line=0), {},
    )
    assert spread["fair_prob"] == ml["fair_prob"]


def test_run_line_missing_line_defaults_to_zero():
    """If the line isn't plumbed into inputs (defensive), the math doesn't
    crash and falls back to the ML-equivalent result."""
    ml = ProbabilityCalculator.calculate_fair_value(
        "ML", "MLB", _bt_inputs(), {},
    )
    run_line = ProbabilityCalculator.calculate_fair_value(
        "Run_Line", "MLB", _bt_inputs(), {},
    )
    assert run_line["fair_prob"] == ml["fair_prob"]


def test_spread_returns_same_dict_shape_as_ml():
    """Spread must return the same keys as ML so Pick objects flow through."""
    ml = ProbabilityCalculator.calculate_fair_value(
        "ML", "NFL", _bt_inputs(), {},
    )
    spread = ProbabilityCalculator.calculate_fair_value(
        "Spread", "NFL", _bt_inputs(line=-3.5), {},
    )
    assert set(spread.keys()) == set(ml.keys())


# ---------------------------------------------------------------------------
# NRFI / YRFI -- first-inning Poisson, complement relationship
# ---------------------------------------------------------------------------

def _nrfi_inputs():
    return {"home_lambda": 1.2, "away_lambda": 1.1}


def test_nrfi_and_yrfi_are_complements():
    """NRFI + YRFI fair_probs must sum to 1.0 on identical inputs -- they
    are two sides of the same first-inning event."""
    nrfi = ProbabilityCalculator.calculate_fair_value(
        "NRFI", "MLB", _nrfi_inputs(), {},
    )
    yrfi = ProbabilityCalculator.calculate_fair_value(
        "YRFI", "MLB", _nrfi_inputs(), {},
    )
    # Clamped to 6 decimals per calculate_fair_value; sum may lose 1 ULP.
    total = nrfi["fair_prob"] + yrfi["fair_prob"]
    assert abs(total - Decimal("1")) < Decimal("0.00001")


def test_nrfi_bounded_by_probability_clamp():
    """Even extreme lambdas should clamp to [0.01, 0.99]."""
    nrfi = ProbabilityCalculator.calculate_fair_value(
        "NRFI", "MLB",
        {"home_lambda": 0.0001, "away_lambda": 0.0001},
        {},
    )
    assert Decimal("0.01") <= nrfi["fair_prob"] <= Decimal("0.99")

    yrfi = ProbabilityCalculator.calculate_fair_value(
        "YRFI", "MLB",
        {"home_lambda": 10.0, "away_lambda": 10.0},
        {},
    )
    assert Decimal("0.01") <= yrfi["fair_prob"] <= Decimal("0.99")


def test_nrfi_returns_same_dict_shape_as_ml():
    """NRFI must return the same keys as ML so Pick objects flow through."""
    ml = ProbabilityCalculator.calculate_fair_value(
        "ML", "MLB", _bt_inputs(), {},
    )
    nrfi = ProbabilityCalculator.calculate_fair_value(
        "NRFI", "MLB", _nrfi_inputs(), {},
    )
    assert set(nrfi.keys()) == set(ml.keys())


# ---------------------------------------------------------------------------
# End-to-end via BettingEngine: away-side Puck_Line must be mirrored
# ---------------------------------------------------------------------------

def _puck_bundle(selection, home_team, away_team, line):
    return FeatureBuilder.build(
        sport="NHL",
        market_type="Puck_Line",
        inputs={
            "strength_home": 1.2,
            "strength_away": 1.0,
            "home_adv": 0.115,
            "line": line,
        },
        universal_features={},
        game_id="NHL-2026-04-23-BOS-TOR",
        selection=selection,
        metadata={"home_team": home_team, "away_team": away_team},
    )


def test_away_puck_line_fair_prob_is_complement_of_home():
    """Same game, same line, home vs away selection -- fair_probs must sum
    to 1 (modulo the 6-decimal quantize). This is the bug the audit
    flagged: previously every Spread/Run_Line/Puck_Line came back with
    the home-centric fair_prob regardless of which side was selected."""
    home_bundle = _puck_bundle("TOR", "TOR", "BOS", line=-1.5)
    away_bundle = _puck_bundle("BOS", "TOR", "BOS", line=-1.5)
    ln = Line(odds=+180, number=Decimal("-1.5"))

    home_pick = BettingEngine.evaluate(home_bundle, ln, public_mode=False)
    away_pick = BettingEngine.evaluate(away_bundle, ln, public_mode=False)

    assert home_pick.fair_prob is not None
    assert away_pick.fair_prob is not None
    total = home_pick.fair_prob + away_pick.fair_prob
    assert abs(total - Decimal("1")) < Decimal("0.00001")


def test_unknown_selection_on_spread_is_ungradeable():
    """Selection that matches neither team leaves fair_prob=None and does
    not post -- same safety behavior as ML."""
    bundle = _puck_bundle("PHI", "TOR", "BOS", line=-1.5)
    ln = Line(odds=+180, number=Decimal("-1.5"))
    pick = BettingEngine.evaluate(bundle, ln, public_mode=False)
    assert pick.fair_prob is None
    assert pick.edge is None
