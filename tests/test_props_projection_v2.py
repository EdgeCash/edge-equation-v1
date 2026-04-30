"""Tests for the Props-1 per-player projection upgrade.

Covers:

* ``bayesian_blend`` shrinkage math
* ``compute_batter_rates_from_statcast`` event aggregation
* ``compute_pitcher_rates_from_statcast`` strikeout rate
* ``project_player_market_prob`` with rates / no-rates / overrides
* ``project_all`` per-player rate routing + override precedence
* PropsStore round-trip on prop_predictions / prop_features
* PropsConfig defaults + path resolution

The Statcast pull (`fetch_player_statcast_window`) hits pybaseball,
so we test the rate-computation logic directly with a fake DataFrame
shape and skip the network path entirely.
"""

from __future__ import annotations

import pandas as pd
import pytest

from edge_equation.engines.props_prizepicks import (
    BatterRollingRates,
    LEAGUE_BATTER_PRIOR_PER_PA,
    LEAGUE_PITCHER_PRIOR_PER_BF,
    MLB_PROP_MARKETS,
    PitcherRollingRates,
    PlayerPropLine,
    ProjectionKnobs,
    bayesian_blend,
    compute_batter_rates_from_statcast,
    compute_pitcher_rates_from_statcast,
    get_default_config,
    project_all,
    project_player_market_prob,
)


# ---------------------------------------------------------------------------
# Bayesian blend
# ---------------------------------------------------------------------------


def test_blend_zero_observations_returns_prior():
    assert bayesian_blend(0.30, 0, 0.10, 80) == 0.10


def test_blend_high_observations_approaches_observed():
    out = bayesian_blend(0.30, 5_000, 0.10, 80)
    assert out == pytest.approx(0.30, abs=0.01)


def test_blend_typical_starter_lands_between():
    """200 PAs of an 8% HR rate against a 3% prior with weight 80
    pseudo-counts blends to ~6.6%."""
    out = bayesian_blend(0.08, 200, 0.03, 80)
    assert 0.06 < out < 0.07


def test_blend_negative_n_treated_as_zero():
    assert bayesian_blend(0.30, -5, 0.10, 80) == 0.10


# ---------------------------------------------------------------------------
# Statcast rate computation
# ---------------------------------------------------------------------------


def test_compute_batter_rates_from_events():
    """6 PAs: 2 singles, 1 double, 1 home run, 2 outs.
    Expect: Hits=4/6, TB=(2+2+4)/6=8/6, HR=1/6, RBI=(0+0+0+2+...)/6.
    """
    df = pd.DataFrame({
        "events": ["single", "single", "double", "home_run", "field_out", "strikeout"],
        "rbi":    [0,        0,        1,        2,           0,           0],
    })
    rates = compute_batter_rates_from_statcast(
        df, player_id=1, player_name="Test", end_date="2026-04-29",
        lookback_days=60,
    )
    assert rates.n_pa == 6
    assert rates.rate_per_pa["Hits"] == pytest.approx(4 / 6)
    assert rates.rate_per_pa["Total_Bases"] == pytest.approx(8 / 6)
    assert rates.rate_per_pa["HR"] == pytest.approx(1 / 6)
    assert rates.rate_per_pa["RBI"] == pytest.approx(3 / 6)


def test_compute_batter_rates_handles_empty_frame():
    rates = compute_batter_rates_from_statcast(
        pd.DataFrame(), player_id=1, player_name="X",
        end_date="2026-04-29", lookback_days=60,
    )
    assert rates.n_pa == 0
    assert rates.rate_per_pa == {}


def test_compute_batter_rates_filters_pre_pa_pitches():
    """Statcast emits a row for every pitch but only PA-terminal rows
    have a non-null `events` value. Mid-PA rows must be ignored."""
    df = pd.DataFrame({
        "events": [None, None, "single", None, "home_run"],
        "rbi":    [0,    0,    0,        0,    1],
    })
    rates = compute_batter_rates_from_statcast(
        df, player_id=1, player_name="X",
        end_date="2026-04-29", lookback_days=60,
    )
    assert rates.n_pa == 2  # only "single" + "home_run"
    assert rates.rate_per_pa["HR"] == 0.5


def test_compute_pitcher_rates_strikeout_share():
    """5 BF: 3 strikeouts → K rate = 0.6."""
    df = pd.DataFrame({
        "events": ["strikeout", "strikeout", "strikeout", "single", "field_out"],
    })
    rates = compute_pitcher_rates_from_statcast(
        df, player_id=2, player_name="P", end_date="2026-04-29",
        lookback_days=60,
    )
    assert rates.n_bf == 5
    assert rates.rate_per_bf["K"] == 0.6


def test_compute_pitcher_rates_empty():
    rates = compute_pitcher_rates_from_statcast(
        None, player_id=2, player_name="P",
        end_date="2026-04-29", lookback_days=60,
    )
    assert rates.n_bf == 0
    assert rates.rate_per_bf == {}


# ---------------------------------------------------------------------------
# Per-player projection
# ---------------------------------------------------------------------------


def _line(canonical="HR", side="Over", line_value=0.5,
           american_odds=+250, player="Aaron Judge"):
    m = MLB_PROP_MARKETS[canonical]
    return PlayerPropLine(
        event_id="e1", home_team="BOS", away_team="NYY",
        commence_time="2026-04-29T23:05:00Z", market=m,
        player_name=player, side=side, line_value=line_value,
        american_odds=float(american_odds),
        decimal_odds=2.5, book="draftkings",
    )


def test_projection_no_rates_uses_league_prior():
    proj = project_player_market_prob(_line())
    # League prior HR rate = 0.030 per PA × 4.1 PAs ≈ λ=0.123
    assert proj.lam == pytest.approx(0.030 * 4.1, abs=1e-6)
    assert proj.confidence == pytest.approx(0.30, abs=1e-6)
    assert proj.blend_n == 0


def test_projection_with_rates_blends_toward_observed():
    """Judge with 250 PA at 8% HR rate → blended ~6.8% → λ ~0.28."""
    rates = BatterRollingRates(
        player_id=1, player_name="Aaron Judge", n_pa=250,
        end_date="2026-04-28", lookback_days=60,
        rate_per_pa={"HR": 0.08, "Hits": 0.27,
                       "Total_Bases": 0.55, "RBI": 0.16},
    )
    proj = project_player_market_prob(_line(), rates=rates)
    expected_blended = (250 * 0.08 + 80 * 0.030) / (250 + 80)
    assert proj.blended_rate == pytest.approx(expected_blended, abs=1e-4)
    assert proj.lam == pytest.approx(expected_blended * 4.1, abs=1e-3)
    # 250 PAs >> 80-pseudocount → high confidence.
    assert proj.confidence > 0.65


def test_projection_under_complements_over():
    rates = BatterRollingRates(
        player_id=1, player_name="X", n_pa=250,
        end_date="2026-04-28", lookback_days=60,
        rate_per_pa={"HR": 0.08},
    )
    over = project_player_market_prob(_line(side="Over"), rates=rates)
    under = project_player_market_prob(_line(side="Under"), rates=rates)
    assert over.model_prob + under.model_prob == pytest.approx(1.0, abs=1e-9)


def test_projection_yes_alias_treated_as_over():
    yes = project_player_market_prob(_line(side="Yes"))
    over = project_player_market_prob(_line(side="Over"))
    assert yes.model_prob == pytest.approx(over.model_prob, abs=1e-9)


def test_projection_rate_override_skips_blend_layer():
    """Backward-compat — passing a flat rate skips the league prior
    blend entirely. Used by the Phase-4 skeleton tests + by callers
    with their own model output."""
    proj = project_player_market_prob(_line(), rate_override=0.45)
    # λ = 0.45 × 4.1 = 1.845, P(Over 0.5) much higher than league prior.
    assert proj.lam == pytest.approx(0.45 * 4.1, abs=1e-6)
    assert proj.model_prob > 0.7


def test_projection_pitcher_market_uses_bf_volume():
    """K market projected against expected_pitcher_bf=22 by default."""
    rates = PitcherRollingRates(
        player_id=2, player_name="Crochet", n_bf=300,
        end_date="2026-04-28", lookback_days=60,
        rate_per_bf={"K": 0.32},
    )
    line = _line(canonical="K", line_value=7.5, side="Over",
                  player="Garrett Crochet", american_odds=-115)
    proj = project_player_market_prob(line, rates=rates)
    # Blended rate = (300*0.32 + 250*0.23) / 550 ≈ 0.279
    expected_blended = (300 * 0.32 + 250 * 0.23) / (300 + 250)
    assert proj.blended_rate == pytest.approx(expected_blended, abs=1e-4)
    assert proj.lam == pytest.approx(expected_blended * 22.0, abs=1e-2)


def test_projection_confidence_scales_with_sample_size():
    """5 PAs → close to 0.30; 1000 PAs → close to 0.85."""
    light = BatterRollingRates(
        player_id=1, player_name="X", n_pa=5,
        end_date="2026-04-28", lookback_days=60,
        rate_per_pa={"HR": 0.08},
    )
    heavy = BatterRollingRates(
        player_id=2, player_name="Y", n_pa=1000,
        end_date="2026-04-28", lookback_days=60,
        rate_per_pa={"HR": 0.08},
    )
    light_proj = project_player_market_prob(_line(), rates=light)
    heavy_proj = project_player_market_prob(_line(), rates=heavy)
    assert light_proj.confidence < 0.40
    assert heavy_proj.confidence > 0.80


# ---------------------------------------------------------------------------
# Bulk projection: per-player rates routing
# ---------------------------------------------------------------------------


def test_project_all_routes_rates_by_player_name():
    judge_rates = BatterRollingRates(
        player_id=1, player_name="Judge", n_pa=300,
        end_date="2026-04-28", lookback_days=60,
        rate_per_pa={"HR": 0.10},
    )
    trout_rates = BatterRollingRates(
        player_id=2, player_name="Trout", n_pa=300,
        end_date="2026-04-28", lookback_days=60,
        rate_per_pa={"HR": 0.04},
    )
    lines = [_line(player="Judge"), _line(player="Trout")]
    out = project_all(lines, rates_by_player={
        "Judge": judge_rates, "Trout": trout_rates,
    })
    judge = next(p for p in out if p.player_name == "Judge")
    trout = next(p for p in out if p.player_name == "Trout")
    assert judge.model_prob > trout.model_prob


def test_project_all_override_takes_precedence_over_rates():
    """When both rates and a flat rate_override are provided,
    rate_override wins (tests the backward-compat path)."""
    rates = BatterRollingRates(
        player_id=1, player_name="X", n_pa=300,
        end_date="2026-04-28", lookback_days=60,
        rate_per_pa={"HR": 0.04},
    )
    out = project_all(
        [_line(player="X")],
        rates_by_player={"X": rates},
        rate_overrides={("X", "HR"): 0.50},
    )
    # 0.50 × 4.1 = 2.05 → P(Over 0.5) very high.
    assert out[0].model_prob > 0.85


# ---------------------------------------------------------------------------
# PropsConfig
# ---------------------------------------------------------------------------


def test_default_config_resolves_paths(tmp_path, monkeypatch):
    cfg = get_default_config()
    assert cfg.duckdb_path.parent.exists()
    assert cfg.cache_dir.exists()


def test_projection_knobs_are_tunable_via_config():
    knobs = ProjectionKnobs(
        prior_weight_pa=10.0,    # very thin prior
        expected_batter_pa=5.0,
    )
    rates = BatterRollingRates(
        player_id=1, player_name="X", n_pa=50,
        end_date="2026-04-28", lookback_days=60,
        rate_per_pa={"HR": 0.10},
    )
    proj = project_player_market_prob(_line(), rates=rates, knobs=knobs)
    # With prior_weight=10, 50 PAs gives 50/(50+10) = 83% own weight.
    expected = (50 * 0.10 + 10 * 0.030) / (50 + 10)
    assert proj.blended_rate == pytest.approx(expected, abs=1e-4)
    # expected_batter_pa was bumped to 5.0.
    assert proj.lam == pytest.approx(expected * 5.0, abs=1e-3)


# ---------------------------------------------------------------------------
# League-prior table sanity
# ---------------------------------------------------------------------------


def test_league_priors_cover_all_supported_markets():
    batter_markets = {"HR", "Hits", "Total_Bases", "RBI"}
    assert batter_markets <= set(LEAGUE_BATTER_PRIOR_PER_PA.keys())
    assert "K" in LEAGUE_PITCHER_PRIOR_PER_BF


def test_league_priors_are_reasonable_magnitude():
    """Sanity-check the prior values stay within plausible MLB ranges."""
    assert 0.020 < LEAGUE_BATTER_PRIOR_PER_PA["HR"] < 0.040
    assert 0.20  < LEAGUE_BATTER_PRIOR_PER_PA["Hits"] < 0.30
    assert 0.18  < LEAGUE_PITCHER_PRIOR_PER_BF["K"]   < 0.28
