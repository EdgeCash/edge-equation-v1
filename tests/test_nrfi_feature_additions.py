"""Tests for the new NRFI feature additions:

* Bottom-3 lineup metrics + lineup-shape gap
* Park × handedness skew interactions
* Lineup-shape × park-runs interaction

Synthetic inputs only — these tests prove the new features compute
the math we claim, with the right signs and the right names. They do
NOT prove the features improve Brier on the live corpus; that's a
separate retrain + audit step the operator runs after merge.
"""

from __future__ import annotations

import pytest


# Heavy ML imports cascade behind feature_engineering — keep the
# module-level import guarded so slim CI passes.
pytest.importorskip("pandas")


# ---------------------------------------------------------------------------
# LineupInputs: new fields exist and have sane defaults
# ---------------------------------------------------------------------------


def test_lineup_inputs_has_bottom3_fields():
    from edge_equation.engines.nrfi.features.feature_engineering import (
        LineupInputs,
    )
    l = LineupInputs()
    # Defaults should not crash; bottom-3 anchors slightly below
    # league top-3 by design (worse hitters → lower OBP/wOBA).
    assert l.bottom3_obp < l.top3_obp
    assert l.bottom3_woba_vs_hand < l.top3_woba_vs_hand
    assert l.bottom3_combined_pa == 0.0


def test_lineup_inputs_explicit_bottom3_values_round_trip():
    from edge_equation.engines.nrfi.features.feature_engineering import (
        LineupInputs,
    )
    l = LineupInputs(bottom3_obp=0.310, bottom3_woba_vs_hand=0.305,
                       bottom3_combined_pa=540.0)
    assert l.bottom3_obp == 0.310
    assert l.bottom3_woba_vs_hand == 0.305
    assert l.bottom3_combined_pa == 540.0


# ---------------------------------------------------------------------------
# _lineup_layer surfaces bottom-3 metrics
# ---------------------------------------------------------------------------


def _make_builder():
    from edge_equation.engines.nrfi.config import get_default_config
    from edge_equation.engines.nrfi.features.feature_engineering import (
        FeatureBuilder,
    )
    cfg = get_default_config()
    return FeatureBuilder(cfg)


def test_lineup_layer_emits_bottom3_features():
    from edge_equation.engines.nrfi.features.feature_engineering import (
        LineupInputs,
    )
    builder = _make_builder()
    l = LineupInputs(top3_obp=0.345, top3_combined_pa=600.0,
                       bottom3_obp=0.290, bottom3_combined_pa=600.0,
                       bottom3_woba_vs_hand=0.288)
    out = builder._lineup_layer("vs_home_p", l, opposing_hand="R")
    assert "vs_home_p_bottom3_obp" in out
    assert "vs_home_p_bottom3_woba_vs_hand" in out
    assert "vs_home_p_lineup_shape_obp_gap" in out


def test_lineup_shape_gap_is_top_minus_bottom():
    """Sign convention: gap > 0 when top-of-order is more productive
    than bottom-of-order (the typical case)."""
    from edge_equation.engines.nrfi.features.feature_engineering import (
        LineupInputs,
    )
    builder = _make_builder()
    # With ample PA on both ends, shrinkage is mild — gap stays positive.
    l = LineupInputs(top3_obp=0.350, top3_combined_pa=1500.0,
                       bottom3_obp=0.290, bottom3_combined_pa=1500.0)
    out = builder._lineup_layer("vs_home_p", l, opposing_hand="R")
    gap = out["vs_home_p_lineup_shape_obp_gap"]
    assert gap > 0.04, f"expected meaningful positive gap, got {gap}"


def test_lineup_shape_gap_zero_for_balanced_lineup():
    from edge_equation.engines.nrfi.features.feature_engineering import (
        LineupInputs,
    )
    builder = _make_builder()
    l = LineupInputs(top3_obp=0.310, top3_combined_pa=900.0,
                       bottom3_obp=0.310, bottom3_combined_pa=900.0)
    out = builder._lineup_layer("vs_home_p", l, opposing_hand="R")
    assert abs(out["vs_home_p_lineup_shape_obp_gap"]) < 0.01


# ---------------------------------------------------------------------------
# Park × handedness interactions
# ---------------------------------------------------------------------------


def _interaction_inputs(*, lh_count, rh_count, park_factor_hr, park_factor_runs,
                          gap=0.0):
    """Build the minimal feature dict the _interactions method reads."""
    return {
        "home_p_xera": 4.0, "away_p_xera": 4.0,
        "home_p_k_pct": 0.225, "away_p_k_pct": 0.225,
        "home_p_bb_pct": 0.085, "away_p_bb_pct": 0.085,
        "ump_zone_idx": 100.0, "ump_abs_overturn": 0.05,
        "wx_wind_signed_axis": 0.0, "wx_temperature_f": 70.0,
        "park_factor_hr": park_factor_hr,
        "park_factor_runs": park_factor_runs,
        "vs_home_p_top3_obp": 0.330, "vs_away_p_top3_obp": 0.330,
        "plat_lhh_count_vs_home_p": float(lh_count),
        "plat_rhh_count_vs_home_p": float(rh_count),
        "plat_lhh_count_vs_away_p": float(lh_count),
        "plat_rhh_count_vs_away_p": float(rh_count),
        "vs_home_p_lineup_shape_obp_gap": gap,
        "vs_away_p_lineup_shape_obp_gap": gap,
    }


def test_park_handedness_interaction_zero_for_neutral_park():
    """Park factor = 1.0 → interaction = 0 regardless of lineup skew."""
    builder = _make_builder()
    f = _interaction_inputs(
        lh_count=6, rh_count=3,
        park_factor_hr=1.0, park_factor_runs=1.0,
    )
    out = builder._interactions(f)
    assert abs(out["int_park_hr_x_lhh_skew_vs_home_p"]) < 1e-9
    assert abs(out["int_park_runs_x_lhh_skew_vs_home_p"]) < 1e-9


def test_park_handedness_interaction_zero_for_balanced_lineup():
    """Balanced lineup (equal L/R counts) → skew = 0 → interaction = 0
    regardless of park factor."""
    builder = _make_builder()
    f = _interaction_inputs(
        lh_count=4, rh_count=4,
        park_factor_hr=1.20, park_factor_runs=1.10,
    )
    out = builder._interactions(f)
    assert abs(out["int_park_hr_x_lhh_skew_vs_home_p"]) < 1e-9
    assert abs(out["int_park_runs_x_lhh_skew_vs_home_p"]) < 1e-9


def test_park_handedness_interaction_positive_for_lhh_in_friendly_park():
    """LHH-heavy lineup (lh > rh) in HR-friendly park (factor > 1) → positive."""
    builder = _make_builder()
    f = _interaction_inputs(
        lh_count=6, rh_count=2,
        park_factor_hr=1.20, park_factor_runs=1.10,
    )
    out = builder._interactions(f)
    assert out["int_park_hr_x_lhh_skew_vs_home_p"] > 0.0
    assert out["int_park_runs_x_lhh_skew_vs_home_p"] > 0.0


def test_park_handedness_interaction_negative_for_rhh_in_friendly_park():
    """Sign convention check: RHH-heavy (lh < rh) gives negative skew →
    multiplied by positive park factor delta → negative interaction."""
    builder = _make_builder()
    f = _interaction_inputs(
        lh_count=2, rh_count=6,
        park_factor_hr=1.20, park_factor_runs=1.10,
    )
    out = builder._interactions(f)
    assert out["int_park_hr_x_lhh_skew_vs_home_p"] < 0.0


def test_park_handedness_emits_both_sides():
    builder = _make_builder()
    f = _interaction_inputs(
        lh_count=5, rh_count=4,
        park_factor_hr=1.10, park_factor_runs=1.05,
    )
    out = builder._interactions(f)
    for side in ("home_p", "away_p"):
        assert f"int_park_hr_x_lhh_skew_vs_{side}" in out
        assert f"int_park_runs_x_lhh_skew_vs_{side}" in out


# ---------------------------------------------------------------------------
# Lineup-shape × park-runs interaction
# ---------------------------------------------------------------------------


def test_lineup_shape_x_park_runs_amplifies_top_heavy_in_run_park():
    """Top-heavy lineup (positive gap) in run-friendly park (>1) →
    positive interaction. Pitcher-friendly park (<1) flips sign."""
    builder = _make_builder()
    f_run = _interaction_inputs(
        lh_count=4, rh_count=4,
        park_factor_hr=1.0, park_factor_runs=1.10, gap=0.05,
    )
    out_run = builder._interactions(f_run)
    assert out_run["int_lineup_shape_x_park_runs_vs_home_p"] > 0.0

    f_pit = _interaction_inputs(
        lh_count=4, rh_count=4,
        park_factor_hr=1.0, park_factor_runs=0.92, gap=0.05,
    )
    out_pit = builder._interactions(f_pit)
    assert out_pit["int_lineup_shape_x_park_runs_vs_home_p"] < 0.0


def test_lineup_shape_x_park_runs_zero_for_balanced_lineup():
    builder = _make_builder()
    f = _interaction_inputs(
        lh_count=4, rh_count=4,
        park_factor_hr=1.0, park_factor_runs=1.10, gap=0.0,
    )
    out = builder._interactions(f)
    assert abs(out["int_lineup_shape_x_park_runs_vs_home_p"]) < 1e-9


# ---------------------------------------------------------------------------
# League prior key was added — make sure the dict still imports
# ---------------------------------------------------------------------------


def test_league_priors_includes_bottom3_anchor():
    from edge_equation.engines.nrfi.features.feature_engineering import (
        LEAGUE_PRIORS,
    )
    assert "obp_bottom3" in LEAGUE_PRIORS
    assert LEAGUE_PRIORS["obp_bottom3"] < LEAGUE_PRIORS["obp_top3"]
