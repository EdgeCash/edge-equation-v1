"""Tests for the props MC band + decomposition Why-note helpers."""

from __future__ import annotations

from edge_equation.engines.props_prizepicks.explain import (
    MCBand,
    decomposition_drivers,
    poisson_mc_band,
)
from edge_equation.engines.props_prizepicks.markets import MLB_PROP_MARKETS
from edge_equation.engines.props_prizepicks.projection import ProjectedSide


def _proj(*, market="Hits", line=1.5, side="Over",
            lam=1.0, blend_n=120, blended_rate=0.245,
            model_prob=0.55, confidence=0.65) -> ProjectedSide:
    return ProjectedSide(
        market=MLB_PROP_MARKETS[market],
        player_name="Test Player",
        line_value=float(line),
        side=side,
        model_prob=float(model_prob),
        confidence=float(confidence),
        lam=float(lam),
        blend_n=int(blend_n),
        blended_rate=float(blended_rate),
    )


# ---------------------------------------------------------------------------
# MC band
# ---------------------------------------------------------------------------


def test_mc_band_returns_low_le_high_in_unit_interval():
    band = poisson_mc_band(_proj(lam=1.0), n_samples=500, seed=7)
    assert isinstance(band, MCBand)
    assert 0.0 <= band.low <= band.high <= 1.0
    assert band.n_samples == 500


def test_mc_band_pp_is_width_in_percentage_points():
    band = poisson_mc_band(_proj(lam=1.0), n_samples=500, seed=7)
    expected = round(max(0.0, band.high - band.low) * 100.0, 1)
    assert band.band_pp == expected
    assert band.band_pp >= 0.0


def test_mc_band_zero_lambda_returns_degenerate_band():
    """λ = 0 → no uncertainty; band collapses to the model probability."""
    proj = _proj(lam=0.0, model_prob=0.10)
    band = poisson_mc_band(proj, n_samples=100, seed=1)
    assert band.low == band.high == 0.10
    assert band.n_samples == 0


def test_mc_band_seed_is_deterministic():
    """Same seed → identical low/high (deterministic for repro tests)."""
    a = poisson_mc_band(_proj(lam=1.5), n_samples=300, seed=42)
    b = poisson_mc_band(_proj(lam=1.5), n_samples=300, seed=42)
    assert a == b


def test_mc_band_under_side_is_complement_of_over():
    """Under's MC band is the complement (1 - sample) of Over's band."""
    over_proj = _proj(side="Over", lam=1.0)
    under_proj = _proj(side="Under", lam=1.0)
    over_band = poisson_mc_band(over_proj, n_samples=500, seed=11)
    under_band = poisson_mc_band(under_proj, n_samples=500, seed=11)
    # Width in pp is identical (sample order is reversed but spread is the same).
    assert over_band.band_pp == under_band.band_pp


# ---------------------------------------------------------------------------
# Decomposition Why-notes
# ---------------------------------------------------------------------------


def test_drivers_no_own_rate_data_calls_out_pure_prior():
    proj = _proj(blend_n=0, blended_rate=0.245)
    drivers = decomposition_drivers(
        proj, league_prior_rate=0.245, expected_volume=4.1, prior_weight=80.0,
    )
    assert any("rests entirely on the league prior" in d for d in drivers)


def test_drivers_high_own_weight_highlights_player_form():
    proj = _proj(blend_n=400, blended_rate=0.31)   # 400 PAs vs 80 prior weight ≈ 83% own
    drivers = decomposition_drivers(
        proj, league_prior_rate=0.245, expected_volume=4.1, prior_weight=80.0,
    )
    head = drivers[0]
    assert "%" in head
    assert "own" in head.lower() or "form" in head.lower()


def test_drivers_balanced_blend_uses_balanced_phrasing():
    proj = _proj(blend_n=80, blended_rate=0.27)   # 50% own, 50% prior
    drivers = decomposition_drivers(
        proj, league_prior_rate=0.245, expected_volume=4.1, prior_weight=80.0,
    )
    assert any("balanced" in d.lower() for d in drivers)


def test_drivers_includes_lambda_buildup_when_volume_and_rate_positive():
    proj = _proj(lam=1.13, blended_rate=0.275, blend_n=120)
    drivers = decomposition_drivers(
        proj, league_prior_rate=0.245, expected_volume=4.1, prior_weight=80.0,
    )
    assert any("→ λ" in d or "λ 1.13" in d for d in drivers)


def test_drivers_skips_lambda_buildup_when_volume_zero():
    proj = _proj(lam=0.0, blended_rate=0.0, blend_n=0)
    drivers = decomposition_drivers(
        proj, league_prior_rate=0.245, expected_volume=0.0, prior_weight=80.0,
    )
    assert not any("→ λ" in d for d in drivers)


def test_drivers_includes_edge_framing_when_market_prob_provided():
    proj = _proj(model_prob=0.58, blend_n=120)
    drivers = decomposition_drivers(
        proj, league_prior_rate=0.245, expected_volume=4.1, prior_weight=80.0,
        market_prob=0.50, edge_pp=8.0,
    )
    edge_bullet = next((d for d in drivers if "edge" in d.lower()), None)
    assert edge_bullet is not None
    assert "+8.0pp" in edge_bullet
    assert "58.0%" in edge_bullet
    assert "50.0%" in edge_bullet


def test_drivers_omits_edge_framing_without_market_prob():
    proj = _proj(blend_n=120)
    drivers = decomposition_drivers(
        proj, league_prior_rate=0.245, expected_volume=4.1, prior_weight=80.0,
        market_prob=None, edge_pp=None,
    )
    assert not any("vs market" in d for d in drivers)


def test_drivers_returns_short_list():
    """Bullets are deliberately capped to keep the email card readable."""
    proj = _proj(blend_n=400)
    drivers = decomposition_drivers(
        proj, league_prior_rate=0.245, expected_volume=4.1, prior_weight=80.0,
        market_prob=0.50, edge_pp=8.0,
    )
    assert 1 <= len(drivers) <= 4
