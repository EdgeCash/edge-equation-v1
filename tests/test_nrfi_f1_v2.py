"""Tests for the NRFI v2 Phase 1 additions.

Covers the four new building blocks:

  * ``features/woolner.py``         - exponential P(0 runs | RPG)
  * ``features/f1_shrinkage.py``    - F1-specific empirical-Bayes shrinkers
  * ``features/splits.py`` (new)    - pitch_mix + F-Strike + umpire F1
  * ``features/feature_engineering.py`` (extended)
                                     - new feature columns reach the output

Mocked frames keep the test fast and offline. The Statcast-shaped
DataFrames are minimal but include the columns each aggregator
documents as required.
"""

from __future__ import annotations

import math

import pytest


pytest.importorskip("pandas")


# ---------------------------------------------------------------------------
# Woolner exponential
# ---------------------------------------------------------------------------


def test_woolner_p_zero_at_zero_rpg_is_one():
    from edge_equation.engines.nrfi.features.woolner import woolner_p_zero
    # No expected runs = 100% chance of NRFI on this side.
    assert woolner_p_zero(0.0) == pytest.approx(1.0, abs=1e-6)


def test_woolner_p_zero_monotone_decreasing_in_rpg():
    """Higher expected RPG must lower P(0 runs)."""
    from edge_equation.engines.nrfi.features.woolner import woolner_p_zero
    rpgs = [0.10, 0.30, 0.51, 0.80, 1.20]
    p_zeros = [woolner_p_zero(r) for r in rpgs]
    assert all(a > b for a, b in zip(p_zeros, p_zeros[1:]))


def test_woolner_p_zero_at_league_average_lands_near_empirical():
    """At league avg ~0.51 R / half-inning, P(0) should be ~0.71
    per the exponential's published fit."""
    from edge_equation.engines.nrfi.features.woolner import woolner_p_zero
    p = woolner_p_zero(0.51)
    assert 0.65 <= p <= 0.75


def test_woolner_distribution_sums_to_one():
    from edge_equation.engines.nrfi.features.woolner import woolner_distribution
    d = woolner_distribution(0.51)
    s = d.p_zero + d.p_one + d.p_two_or_more
    assert s == pytest.approx(1.0, abs=0.05)


def test_nrfi_probability_two_independent_halves():
    """P(NRFI) = P(top zero) * P(bottom zero) under independence."""
    from edge_equation.engines.nrfi.features.woolner import (
        nrfi_probability, woolner_p_zero,
    )
    top, bot = 0.45, 0.60
    expected = woolner_p_zero(top) * woolner_p_zero(bot)
    assert nrfi_probability(top, bot) == pytest.approx(expected, abs=1e-9)


def test_yrfi_probability_complements_nrfi():
    from edge_equation.engines.nrfi.features.woolner import (
        nrfi_probability, yrfi_probability,
    )
    top, bot = 0.40, 0.55
    assert (nrfi_probability(top, bot) + yrfi_probability(top, bot)
            == pytest.approx(1.0, abs=1e-9))


# ---------------------------------------------------------------------------
# F1 empirical-Bayes shrinkage
# ---------------------------------------------------------------------------


def test_zero_sample_returns_prior_for_every_shrinker():
    from edge_equation.engines.nrfi.features.f1_shrinkage import (
        league_priors, shrink_f1_bb_pct, shrink_f1_hr_pct, shrink_f1_k_pct,
        shrink_f1_runs_per_inn, shrink_ump_f1_csa, shrink_ump_f1_walk_rate,
    )
    p = league_priors()
    # Use wildly off "observed" values; with sample==0 the shrinker
    # must return the prior regardless.
    assert shrink_f1_k_pct(0.95, 0) == p["k_pct"]
    assert shrink_f1_bb_pct(0.95, 0) == p["bb_pct"]
    assert shrink_f1_hr_pct(0.95, 0) == p["hr_pct"]
    assert shrink_f1_runs_per_inn(5.0, 0) == p["runs_per_inn"]
    assert shrink_ump_f1_csa(0.5, 0) == p["ump_csa"]
    assert shrink_ump_f1_walk_rate(0.5, 0) == p["ump_walk_rate"]


def test_huge_sample_collapses_to_observation():
    from edge_equation.engines.nrfi.features.f1_shrinkage import (
        shrink_f1_bb_pct, shrink_f1_k_pct,
    )
    # 100k PAs is enough that the shrunk value sits within 1% of obs.
    assert shrink_f1_k_pct(0.40, 100_000) == pytest.approx(0.40, abs=0.01)
    assert shrink_f1_bb_pct(0.05, 100_000) == pytest.approx(0.05, abs=0.01)


def test_half_shrink_point_for_bb_pct_is_70_pa():
    """At sample = half-shrink (~70 PAs), shrunk value is the
    midpoint of prior and observation."""
    from edge_equation.engines.nrfi.features.f1_shrinkage import (
        league_priors, shrink_f1_bb_pct,
    )
    p = league_priors()["bb_pct"]
    obs = 0.20
    shrunk = shrink_f1_bb_pct(obs, 70.0)
    assert shrunk == pytest.approx((obs + p) / 2.0, abs=1e-6)


def test_ump_f1_csa_shrinks_toward_zero():
    """Umpire CSA is a delta from neutral, so the prior is 0."""
    from edge_equation.engines.nrfi.features.f1_shrinkage import (
        shrink_ump_f1_csa,
    )
    # Big positive observed CSA, low sample --> shrinks toward 0.
    shrunk = shrink_ump_f1_csa(0.50, 30.0)
    assert 0.0 < shrunk < 0.50  # between prior (0) and obs (0.50)


# ---------------------------------------------------------------------------
# splits.py new aggregators
# ---------------------------------------------------------------------------


def _statcast_pitch_frame():
    """Minimal Statcast-shaped frame with three pitchers + two umpires."""
    import pandas as pd
    rows = [
        # Pitcher 1: 6 pitches, 2 PAs --- mostly fastball
        {"pitcher": 1, "pitch_type": "FF", "pitch_number": 1,
         "description": "called_strike", "events": None, "zone": 5,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "FF", "pitch_number": 2,
         "description": "ball", "events": None, "zone": 12,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "FF", "pitch_number": 3,
         "description": "swinging_strike", "events": "strikeout", "zone": 5,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "SI", "pitch_number": 1,
         "description": "ball", "events": None, "zone": 13,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "CU", "pitch_number": 2,
         "description": "called_strike", "events": None, "zone": 5,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "CU", "pitch_number": 3,
         "description": "hit_into_play", "events": "single", "zone": 5,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        # Pitcher 2: pure fastball-only (1 PA)
        {"pitcher": 2, "pitch_type": "FF", "pitch_number": 1,
         "description": "ball", "events": None, "zone": 12,
         "home_plate_umpire_id": 200, "game_pk": 2, "post_bat_score": 0},
        {"pitcher": 2, "pitch_type": "FF", "pitch_number": 2,
         "description": "ball", "events": None, "zone": 12,
         "home_plate_umpire_id": 200, "game_pk": 2, "post_bat_score": 0},
        {"pitcher": 2, "pitch_type": "FF", "pitch_number": 3,
         "description": "ball", "events": None, "zone": 12,
         "home_plate_umpire_id": 200, "game_pk": 2, "post_bat_score": 0},
        {"pitcher": 2, "pitch_type": "FF", "pitch_number": 4,
         "description": "ball", "events": "walk", "zone": 12,
         "home_plate_umpire_id": 200, "game_pk": 2, "post_bat_score": 0},
    ]
    return pd.DataFrame(rows)


def test_first_inning_pitch_mix_distributes_correctly():
    from edge_equation.engines.nrfi.features.splits import (
        first_inning_pitch_mix,
    )
    df = _statcast_pitch_frame()
    out = first_inning_pitch_mix(df, pitcher_id=1)
    # Pitcher 1 threw 6 pitches: 3 FF + 1 SI + 2 CU.
    assert out["p1_mix_ff_pct"] == pytest.approx(3 / 6, abs=1e-6)
    assert out["p1_mix_si_pct"] == pytest.approx(1 / 6, abs=1e-6)
    assert out["p1_mix_cu_pct"] == pytest.approx(2 / 6, abs=1e-6)
    assert out["p1_mix_pitches"] == 6.0
    # Three pitch types each at >= 5% --- arsenal_depth = 3.
    assert out["p1_arsenal_depth"] == 3.0


def test_first_inning_pitch_mix_empty_returns_neutral_prior():
    from edge_equation.engines.nrfi.features.splits import (
        first_inning_pitch_mix,
    )
    out = first_inning_pitch_mix(None, pitcher_id=999)
    assert out["p1_mix_ff_pct"] == pytest.approx(0.40, abs=1e-6)
    assert out["p1_mix_pitches"] == 0.0


def test_first_inning_f_strike_pct_counts_first_pitch_strikes():
    from edge_equation.engines.nrfi.features.splits import (
        first_inning_f_strike_pct,
    )
    df = _statcast_pitch_frame()
    # Pitcher 1, pitch_number==1: rows 0 (called_strike), 3 (ball), 6 (NA -- wait, that's pitcher 2)
    # Pitcher 1 first-pitches: row 0 (called_strike) + row 3 (ball) -> 2 PAs, 1 strike
    out = first_inning_f_strike_pct(df, pitcher_id=1)
    assert out["p1_f_strike_pct"] == pytest.approx(0.5, abs=1e-6)
    assert out["p1_f_strike_sample_pa"] == 2.0


def test_umpire_first_inning_stats_walk_rate_for_wild_ump():
    from edge_equation.engines.nrfi.features.splits import (
        umpire_first_inning_stats,
    )
    df = _statcast_pitch_frame()
    # Umpire 200 saw 4 pitches across 1 PA, all called balls, ending in a walk.
    out = umpire_first_inning_stats(df, ump_id=200)
    assert out["ump_f1_walk_rate"] == pytest.approx(1.0, abs=1e-6)
    assert out["ump_f1_pa"] == 1.0


def test_umpire_first_inning_stats_returns_empty_when_no_ump_column():
    """Statcast pitch frames don't always carry the umpire id (the
    home_plate_umpire scraper hasn't merged it yet, older backfill
    snapshots predate the join). The function must NOT crash with a
    KeyError --- it must return the empty default so the EB shrinker
    can regress to the league mean.

    Regression guard for the ``Feature build failed for game XXXX:
    'ump_id'`` warning that flooded the NRFI Weekly Retrain log."""
    import pandas as pd
    from edge_equation.engines.nrfi.features.splits import (
        umpire_first_inning_stats,
    )
    # Frame has all the pitch columns the function uses, but no umpire
    # id column at all.
    df = pd.DataFrame([
        {"pitcher": 1, "pitch_type": "FF", "pitch_number": 1,
         "description": "called_strike", "events": None, "zone": 5,
         "game_pk": 1, "post_bat_score": 0},
    ])
    out = umpire_first_inning_stats(df, ump_id=42)
    # Returns the empty default --- sample sizes 0, neutral CSA, league
    # avg walk rate. Most importantly: NO RAISE.
    assert out["ump_f1_pa"] == 0.0
    assert out["ump_f1_called"] == 0.0
    assert out["ump_f1_csa"] == 0.0
    assert out["ump_f1_walk_rate"] == pytest.approx(0.085, abs=1e-6)


# ---------------------------------------------------------------------------
# FeatureBuilder produces the new columns
# ---------------------------------------------------------------------------


def _minimal_inputs():
    """Build the smallest set of inputs the FeatureBuilder accepts."""
    from edge_equation.engines.nrfi.config import NRFIConfig
    from edge_equation.engines.nrfi.features.feature_engineering import (
        FeatureBuilder, GameContext, LineupInputs, PitcherInputs,
        UmpireInputs,
    )
    from edge_equation.engines.nrfi.data.park_factors import ParkInfo
    builder = FeatureBuilder(NRFIConfig())
    home_p = PitcherInputs(pitcher_id=1, hand="R", season_batters_faced=600)
    away_p = PitcherInputs(pitcher_id=2, hand="L", season_batters_faced=600,
                            is_opener=True)
    home_l = LineupInputs(top3_combined_pa=400, confirmed=True, source="confirmed")
    away_l = LineupInputs(top3_combined_pa=400, confirmed=True, source="confirmed")
    ump = UmpireInputs(
        ump_id=42, full_name="Test", f1_csa=0.05, f1_walk_rate=0.10,
        f1_called_sample=200.0, f1_pa_sample=80.0,
    )
    # Use a registered tricode --- the feature builder looks the
    # park up in the canonical ``PARKS`` map for downstream
    # adjustments and unrecognized codes raise.
    from edge_equation.engines.nrfi.data.park_factors import PARKS
    park = PARKS["ARI"]
    ctx = GameContext(
        game_pk=1, game_date="2026-05-07", season=2026,
        home_team="HOM", away_team="AWY", park=park,
    )
    return builder, home_p, away_p, home_l, away_l, ump, ctx


def test_pitcher_layer_emits_pitch_mix_features():
    builder, home_p, away_p, home_l, away_l, ump, ctx = _minimal_inputs()
    feats = builder.build(
        ctx=ctx, home_pitcher=home_p, away_pitcher=away_p,
        home_lineup=home_l, away_lineup=away_l, umpire=ump,
    )
    for code in ("ff", "si", "fc", "cu", "ch"):
        assert f"home_p_f1_mix_{code}" in feats
        assert f"away_p_f1_mix_{code}" in feats
    assert "home_p_f_strike_pct" in feats
    assert "away_p_f_strike_pct" in feats


def test_pitcher_layer_emits_opener_flag():
    builder, home_p, away_p, home_l, away_l, ump, ctx = _minimal_inputs()
    feats = builder.build(
        ctx=ctx, home_pitcher=home_p, away_pitcher=away_p,
        home_lineup=home_l, away_lineup=away_l, umpire=ump,
    )
    assert feats["home_p_is_opener"] == 0.0
    assert feats["away_p_is_opener"] == 1.0


def test_umpire_layer_emits_f1_specific_features():
    builder, home_p, away_p, home_l, away_l, ump, ctx = _minimal_inputs()
    feats = builder.build(
        ctx=ctx, home_pitcher=home_p, away_pitcher=away_p,
        home_lineup=home_l, away_lineup=away_l, umpire=ump,
    )
    assert "ump_f1_csa" in feats
    assert "ump_f1_walk_rate" in feats
    assert "ump_f1_called_sample" in feats
    assert "ump_f1_pa_sample" in feats


def test_interactions_layer_emits_f_strike_x_ump_f1_csa():
    builder, home_p, away_p, home_l, away_l, ump, ctx = _minimal_inputs()
    feats = builder.build(
        ctx=ctx, home_pitcher=home_p, away_pitcher=away_p,
        home_lineup=home_l, away_lineup=away_l, umpire=ump,
    )
    assert "int_home_p_f_strike_x_ump_f1_csa" in feats
    assert "int_away_p_f_strike_x_ump_f1_csa" in feats


def test_interactions_layer_emits_woolner_nrfi_prior():
    """The Woolner prior is the calibration head we want to use as a
    monotone, well-calibrated baseline that the GBT can lean on."""
    builder, home_p, away_p, home_l, away_l, ump, ctx = _minimal_inputs()
    feats = builder.build(
        ctx=ctx, home_pitcher=home_p, away_pitcher=away_p,
        home_lineup=home_l, away_lineup=away_l, umpire=ump,
    )
    p = feats["woolner_nrfi_prior"]
    assert 0.0 < p < 1.0
    # League-mean inputs should put us near the historical NRFI rate
    # (~0.535) within a wide tolerance.
    assert 0.40 < p < 0.65


def test_pitcher_layer_shrunk_f1_kpct_lies_between_raw_and_prior():
    """With a small F1 sample the shrunk value sits between observation
    and prior (0.220)."""
    from edge_equation.engines.nrfi.features.feature_engineering import (
        FeatureBuilder, PitcherInputs,
    )
    from edge_equation.engines.nrfi.config import NRFIConfig
    p = PitcherInputs(
        pitcher_id=1, hand="R", season_batters_faced=600,
        first_inn_stats={
            "p1_inn_pa": 30,         # thin F1 sample
            "p1_inn_k_pct": 0.50,    # extreme observation
            "p1_inn_bb_pct": 0.05,
            "p1_inn_hr_pct": 0.02,
            "p1_inn_runs_per": 0.30,
        },
    )
    builder = FeatureBuilder(NRFIConfig())
    layer = builder._pitcher_layer("home_p", p)
    raw = layer["home_p_first_inn_k_pct_raw"]
    shrunk = layer["home_p_first_inn_k_pct"]
    assert raw == pytest.approx(0.50, abs=1e-6)
    # League prior is 0.22; shrunk should be between prior and obs.
    assert 0.22 < shrunk < 0.50
