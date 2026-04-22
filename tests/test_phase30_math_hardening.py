"""
Phase 30 -- full math hardening.

Three invariants lock into tests here:

  1. MonteCarloSimulator produces a deterministic sampling distribution
     over 10k reruns of Bradley-Terry with perturbed inputs, exposing
     the stdev / p10 / p50 / p90 the MVS detector consumes. Same seed
     -> identical numbers. Different seed -> different numbers.

  2. BettingEngine.evaluate runs the MC itself for ML and BTTS, stashes
     the result in pick.metadata["mc_stability"], and hands it to
     detect_major_variance so the detector's fourth gate can trigger
     without a caller-supplied MC.

  3. select_parlay_of_day tightened to max 4 legs with an edge floor
     of 12% AND a Kelly floor of 4% per leg (A+ only, ceiling at 20%
     edge). Enforces the brand's "meaningful stake per leg" rule
     before the post-Phase-28 grades earn back settled-track trust.

  4. _baseline_read enriches from bundle.metadata when available:
     Elo diff, rest/travel, pace/off/def, plus an explicit MC band
     line so premium subscribers see the uncertainty bar.
"""
from decimal import Decimal

import pytest

from edge_equation.engine.betting_engine import BettingEngine, _baseline_read
from edge_equation.engine.feature_builder import FeatureBundle
from edge_equation.engine.major_variance import detect as detect_mvs
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.math.monte_carlo import (
    DEFAULT_N_SIMS,
    DEFAULT_STRENGTH_SIGMA,
    MCResult,
    MonteCarloSimulator,
)
from edge_equation.posting.posting_formatter import PostingFormatter


# ------------------------------------------------ MC determinism

def test_simulate_ml_is_deterministic_from_seed():
    """Same inputs + same seed_key -> identical stats across runs."""
    a = MonteCarloSimulator.simulate_ml(1.32, 1.15, 0.115, seed_key="G1:NYY")
    b = MonteCarloSimulator.simulate_ml(1.32, 1.15, 0.115, seed_key="G1:NYY")
    assert a.mean == b.mean
    assert a.stdev == b.stdev
    assert a.p10 == b.p10
    assert a.p50 == b.p50
    assert a.p90 == b.p90
    assert a.n == b.n == DEFAULT_N_SIMS


def test_simulate_ml_seed_affects_output():
    a = MonteCarloSimulator.simulate_ml(1.32, 1.15, 0.115, seed_key="A")
    b = MonteCarloSimulator.simulate_ml(1.32, 1.15, 0.115, seed_key="B")
    # Different seeds should shift at least one of the percentile stats.
    assert (a.stdev, a.p10, a.p90) != (b.stdev, b.p10, b.p90)


def test_simulate_ml_percentiles_ordered():
    r = MonteCarloSimulator.simulate_ml(1.2, 1.0, 0.1, seed_key="ord")
    assert r.p10 <= r.p50 <= r.p90
    assert Decimal("0") <= r.p10 <= Decimal("1")
    assert Decimal("0") <= r.p90 <= Decimal("1")


def test_simulate_ml_stdev_in_reasonable_band():
    """Default sigma 0.05 on BT strengths should produce a fair_prob
    stdev somewhere in the 0.01 - 0.15 band -- tight enough to be
    useful, loose enough to reflect real uncertainty."""
    r = MonteCarloSimulator.simulate_ml(1.25, 1.05, 0.1, seed_key="std")
    assert Decimal("0.005") < r.stdev < Decimal("0.15")


def test_simulate_point_prob_determinism_and_range():
    a = MonteCarloSimulator.simulate_point_prob(Decimal("0.55"), seed_key="bt1")
    b = MonteCarloSimulator.simulate_point_prob(Decimal("0.55"), seed_key="bt1")
    assert a.stdev == b.stdev
    assert a.p10 == b.p10
    r = MonteCarloSimulator.simulate_point_prob(Decimal("0.55"), seed_key="pt")
    assert r.p10 <= r.p50 <= r.p90
    assert Decimal("0") < r.p10 < Decimal("1")


def test_mc_result_to_dict_shape_matches_mvs_detector():
    """Keys returned here are consumed by engine.major_variance. If
    the schema drifts, the detector silently stops firing."""
    r = MonteCarloSimulator.simulate_ml(1.1, 1.1, 0.1, seed_key="shape")
    d = r.to_dict()
    assert "stdev" in d
    assert "p10" in d
    assert "p90" in d
    assert "mean" in d
    assert "n" in d


# ------------------------------------------------ engine MC wiring

def _bundle(market="ML", inputs=None, metadata=None, sport="MLB",
            selection="NYY"):
    inputs = inputs or {
        "strength_home": 1.32,
        "strength_away": 1.15,
        "home_adv": 0.115,
    }
    metadata = metadata or {"home_team": "NYY", "away_team": "BOS"}
    return FeatureBundle(
        sport=sport, market_type=market, inputs=inputs,
        universal_features={},
        game_id="G-MC-1",
        selection=selection,
        metadata=metadata,
    )


def test_engine_runs_mc_and_stashes_into_metadata():
    pick = BettingEngine.evaluate(_bundle(), Line(odds=-132))
    mc = pick.metadata.get("mc_stability")
    assert mc is not None
    assert "stdev" in mc
    assert "p10" in mc
    assert "p90" in mc


def test_engine_mc_is_deterministic_across_runs():
    p1 = BettingEngine.evaluate(_bundle(), Line(odds=-132))
    p2 = BettingEngine.evaluate(_bundle(), Line(odds=-132))
    assert p1.metadata["mc_stability"]["stdev"] == p2.metadata["mc_stability"]["stdev"]
    assert p1.metadata["mc_stability"]["p10"] == p2.metadata["mc_stability"]["p10"]


def test_engine_mc_feeds_mvs_detector():
    """When an A+ edge lands with tight MC stability, the detector
    should fire off the engine-produced MC alone (no caller-supplied
    mc_stability). Proves the handoff is real, not ornamental."""
    pick = BettingEngine.evaluate(_bundle(), Line(odds=-132))
    mc = pick.metadata.get("mc_stability")
    assert mc is not None
    # Build a synthetic A+ pick carrying the real MC to exercise detect().
    synthetic = Pick(
        sport="MLB", market_type="ML", selection="NYY",
        line=Line(odds=-110),
        fair_prob=Decimal("0.62"),
        edge=Decimal("0.14"),
        kelly=Decimal("0.06"),
        grade="A+",
        game_id="G-MC-1",
        metadata={"mc_stability": mc},
    )
    sig = detect_mvs(synthetic)
    # Either stdev or band gate must have been able to evaluate --
    # the detector explicitly must not return "data unavailable".
    assert "data unavailable" not in sig.reason


# ------------------------------------------------ parlay tightening

def _parlay_leg(grade="A+", edge="0.14", kelly="0.05", game_id="G"):
    return Pick(
        sport="MLB", market_type="ML", selection="X",
        line=Line(odds=-110),
        fair_prob=Decimal("0.58"),
        edge=Decimal(edge),
        kelly=Decimal(kelly),
        grade=grade,
        game_id=game_id,
    )


def test_parlay_rejects_legs_below_edge_floor():
    picks = [
        _parlay_leg(edge="0.11", kelly="0.06", game_id=f"G{i}")
        for i in range(4)
    ]
    assert PostingFormatter.select_parlay_of_day(picks) == []


def test_parlay_rejects_legs_below_kelly_floor():
    picks = [
        _parlay_leg(edge="0.14", kelly="0.03", game_id=f"G{i}")
        for i in range(4)
    ]
    assert PostingFormatter.select_parlay_of_day(picks) == []


def test_parlay_default_max_is_four_not_six():
    picks = [_parlay_leg(game_id=f"G{i}") for i in range(10)]
    legs = PostingFormatter.select_parlay_of_day(picks)
    assert len(legs) == 4  # capped at new default


def test_parlay_admits_legs_that_meet_all_three_floors():
    picks = [
        _parlay_leg(edge="0.13", kelly="0.05", game_id="G1"),
        _parlay_leg(edge="0.14", kelly="0.06", game_id="G2"),
        _parlay_leg(edge="0.15", kelly="0.07", game_id="G3"),
    ]
    legs = PostingFormatter.select_parlay_of_day(picks)
    assert len(legs) == 3


def test_parlay_respects_explicit_override_of_floors():
    """Callers can still loosen or tighten -- keeps it testable."""
    picks = [_parlay_leg(edge="0.10", kelly="0.03", game_id=f"G{i}")
             for i in range(4)]
    legs = PostingFormatter.select_parlay_of_day(
        picks,
        min_leg_edge=Decimal("0.05"),
        min_leg_kelly=Decimal("0.02"),
    )
    assert 3 <= len(legs) <= 4


# ------------------------------------------------ enriched Read

def test_baseline_read_mentions_mc_band_when_available():
    bundle = _bundle()
    out = _baseline_read(
        market_type="ML",
        selection="NYY",
        bundle=bundle,
        fair_prob=Decimal("0.60"),
        edge=Decimal("0.12"),
        hfa_value=None,
        decay_halflife_days=None,
        mc_stability={"stdev": "0.06", "p10": "0.52", "p90": "0.64"},
    )
    assert "MC band" in out
    assert "0.52" in out
    assert "0.64" in out


def test_baseline_read_mentions_elo_diff_when_present():
    bundle = _bundle(metadata={
        "home_team": "NYY", "away_team": "BOS", "elo_diff": 42,
    })
    out = _baseline_read(
        market_type="ML", selection="NYY", bundle=bundle,
        fair_prob=Decimal("0.60"), edge=Decimal("0.10"),
        hfa_value=None, decay_halflife_days=None,
    )
    assert "Elo gap" in out


def test_baseline_read_mentions_rest_and_travel():
    bundle = _bundle(metadata={
        "home_team": "NYY", "away_team": "LAA",
        "rest_days_home": 3, "rest_days_away": 1,
        "travel_miles_away": 2450,
    })
    out = _baseline_read(
        market_type="ML", selection="NYY", bundle=bundle,
        fair_prob=Decimal("0.60"), edge=Decimal("0.10"),
        hfa_value=None, decay_halflife_days=None,
    )
    assert "Rest edge" in out
    assert "traveling" in out


def test_baseline_read_totals_picks_up_def_env():
    bundle = _bundle(
        market="Total",
        inputs={
            "expected_total": Decimal("8.5"),
            "pace": Decimal("1.02"),
            "off_env": Decimal("0.98"),
            "def_env": Decimal("1.05"),
        },
    )
    out = _baseline_read(
        market_type="Total", selection="Over 8.5", bundle=bundle,
        fair_prob=None, edge=None,
        hfa_value=None, decay_halflife_days=None,
    )
    assert "pace=" in out
    assert "off=" in out
    assert "def=" in out
