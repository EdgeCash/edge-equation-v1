"""Tests for the full-game MC band + decomposition Why-note helpers."""

from __future__ import annotations

from edge_equation.engines.full_game.explain import (
    MCBand,
    decomposition_drivers,
    mc_band,
)
from edge_equation.engines.full_game.markets import MLB_FULL_GAME_MARKETS
from edge_equation.engines.full_game.projection import ProjectedFullGameSide


def _proj(*, market="Total", side="Over", line=8.5,
            lam_home=4.7, lam_away=4.55, lam_used=9.25,
            blend_n_home=30, blend_n_away=28,
            model_prob=0.55, confidence=0.65) -> ProjectedFullGameSide:
    return ProjectedFullGameSide(
        market=MLB_FULL_GAME_MARKETS[market],
        side=side,
        line_value=line,
        model_prob=float(model_prob),
        confidence=float(confidence),
        lam_home=float(lam_home),
        lam_away=float(lam_away),
        lam_used=float(lam_used),
        blend_n_home=int(blend_n_home),
        blend_n_away=int(blend_n_away),
    )


# ---------------------------------------------------------------------------
# MC band
# ---------------------------------------------------------------------------


def test_mc_band_total_returns_low_le_high_in_unit_interval():
    band = mc_band(_proj(market="Total", line=8.5),
                    line_value=8.5, n_samples=500, seed=7)
    assert isinstance(band, MCBand)
    assert 0.0 <= band.low <= band.high <= 1.0


def test_mc_band_pp_is_width_in_percentage_points():
    band = mc_band(_proj(market="Total", line=8.5),
                    line_value=8.5, n_samples=500, seed=7)
    expected = round(max(0.0, band.high - band.low) * 100.0, 1)
    assert band.band_pp == expected
    assert band.band_pp >= 0.0


def test_mc_band_zero_lambda_returns_degenerate_band():
    proj = _proj(lam_home=0.0, lam_away=0.0, model_prob=0.10)
    band = mc_band(proj, line_value=8.5, n_samples=100, seed=1)
    assert band.low == band.high == 0.10
    assert band.n_samples == 0


def test_mc_band_seed_is_deterministic():
    a = mc_band(_proj(), line_value=8.5, n_samples=300, seed=42)
    b = mc_band(_proj(), line_value=8.5, n_samples=300, seed=42)
    assert a == b


def test_mc_band_ml_works_without_line_value():
    proj = _proj(market="ML", side="NYY", line=None,
                  lam_home=4.7, lam_away=4.55)
    band = mc_band(proj, is_home_side=True, n_samples=500, seed=11)
    assert 0.0 <= band.low <= band.high <= 1.0
    assert band.band_pp >= 0.0


def test_mc_band_run_line_uses_line_value():
    proj = _proj(market="Run_Line", side="NYY", line=-1.5)
    band = mc_band(proj, line_value=-1.5, is_home_side=True,
                    n_samples=500, seed=11)
    assert 0.0 <= band.low <= band.high <= 1.0


def test_mc_band_team_total_home_vs_away_uses_correct_lambda():
    """Team_Total Over for home should use λ_home; Over for away uses λ_away."""
    proj = _proj(market="Team_Total", side="Over", line=4.5,
                  lam_home=5.5, lam_away=4.0)
    home_band = mc_band(proj, line_value=4.5, is_home_side=True,
                          n_samples=500, seed=11)
    away_band = mc_band(proj, line_value=4.5, is_home_side=False,
                          n_samples=500, seed=11)
    # Higher λ → wider Poisson spread, but not necessarily wider band.
    # The point is they're different (the home/away switch matters).
    assert home_band != away_band


# ---------------------------------------------------------------------------
# Decomposition Why-notes
# ---------------------------------------------------------------------------


def test_drivers_no_team_data_calls_out_pure_prior():
    proj = _proj(blend_n_home=0, blend_n_away=0)
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
    )
    assert any("rests entirely on the league prior" in d for d in drivers)


def test_drivers_high_team_signal_highlights_form():
    proj = _proj(blend_n_home=40, blend_n_away=40)
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
    )
    head = drivers[0]
    assert "%" in head
    assert "team" in head.lower() or "form" in head.lower()


def test_drivers_balanced_blend_uses_balanced_phrasing():
    proj = _proj(blend_n_home=12, blend_n_away=12)   # 50/50
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
    )
    assert any("balanced" in d.lower() for d in drivers)


def test_drivers_uses_min_of_home_and_away_blend_n():
    """Confidence floor — one team on a thin sample limits the call."""
    proj = _proj(blend_n_home=40, blend_n_away=2)
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
    )
    head = drivers[0]
    # min(40, 2) = 2 → still leans heavily on prior.
    assert "thin sample" in head.lower() or "league prior" in head.lower()


def test_drivers_total_market_includes_lambda_buildup():
    proj = _proj(market="Total", lam_home=4.85, lam_away=4.20, blend_n_home=30, blend_n_away=30)
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
    )
    assert any("4.85" in d and "4.20" in d for d in drivers)


def test_drivers_ml_market_uses_skellam_phrasing():
    proj = _proj(market="ML", side="NYY", line=None, blend_n_home=30, blend_n_away=30)
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
    )
    assert any("skellam" in d.lower() for d in drivers)


def test_drivers_includes_edge_framing_when_market_prob_provided():
    proj = _proj(model_prob=0.58, blend_n_home=30, blend_n_away=30)
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
        market_prob=0.50, edge_pp=8.0,
    )
    edge_bullet = next((d for d in drivers if "edge" in d.lower()), None)
    assert edge_bullet is not None
    assert "+8.0pp" in edge_bullet
    assert "58.0%" in edge_bullet


def test_drivers_omits_edge_framing_without_market_prob():
    proj = _proj(blend_n_home=30, blend_n_away=30)
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
        market_prob=None, edge_pp=None,
    )
    assert not any("vs market" in d for d in drivers)


def test_drivers_returns_short_list():
    proj = _proj(blend_n_home=30, blend_n_away=30)
    drivers = decomposition_drivers(
        proj, home_tricode="NYY", away_tricode="BOS", prior_weight=12.0,
        market_prob=0.50, edge_pp=8.0,
    )
    assert 1 <= len(drivers) <= 4
