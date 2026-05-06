"""Tests for the props calibration shrink + Premium-floor edge builder."""
from __future__ import annotations

import pytest

from edge_equation.engines.props_prizepicks.edge import (
    PropEdgePick, build_edge_picks,
)
from edge_equation.engines.props_prizepicks.markets import MLB_PROP_MARKETS
from edge_equation.engines.props_prizepicks.odds_fetcher import PlayerPropLine
from edge_equation.engines.props_prizepicks.projection import (
    DEFAULT_PROPS_TEMPERATURE, calibrate_prob, shrink_prob_toward_market,
    ProjectedSide,
)
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------
# Pure shrink math
# ---------------------------------------------------------------------

def test_shrink_pulls_toward_market_not_05():
    # Market thinks 0.40, model says 0.70, tau=0.5 -> midpoint.
    out = shrink_prob_toward_market(0.70, 0.40, 0.5)
    assert out == pytest.approx(0.55)


def test_shrink_tau_one_is_identity():
    assert shrink_prob_toward_market(0.65, 0.40, 1.0) == pytest.approx(0.65)


def test_shrink_tau_zero_collapses_to_market():
    assert shrink_prob_toward_market(0.85, 0.42, 0.0) == pytest.approx(0.42)


def test_shrink_clips_tau_to_unit_interval():
    # tau outside [0, 1] should clip, not blow up
    assert shrink_prob_toward_market(0.7, 0.4, -1.0) == pytest.approx(0.4)
    assert shrink_prob_toward_market(0.7, 0.4, 5.0) == pytest.approx(0.7)


def test_calibrate_prob_uses_per_market_default():
    # HR is the most aggressive shrinker (tau=0.55).
    cal_hr = calibrate_prob(0.70, 0.45, "HR")
    cal_k  = calibrate_prob(0.70, 0.45, "K")  # tau=0.75, less shrink
    # Both pulled toward 0.45; HR pulled harder -> lower result.
    assert cal_hr < cal_k


def test_calibrate_prob_unknown_market_is_identity():
    assert calibrate_prob(0.7, 0.4, "exotic") == pytest.approx(0.7)


# ---------------------------------------------------------------------
# build_edge_picks integration
# ---------------------------------------------------------------------

@pytest.fixture
def synth_hr_pair():
    hr = MLB_PROP_MARKETS["HR"]
    lines = [
        PlayerPropLine(
            event_id="e1", commence_time=None, away_team="AAA",
            home_team="BBB", market=hr, player_name="Player",
            line_value=0.5, side="Over", american_odds=-120,
            decimal_odds=1.833, book="fanduel",
        ),
        PlayerPropLine(
            event_id="e1", commence_time=None, away_team="AAA",
            home_team="BBB", market=hr, player_name="Player",
            line_value=0.5, side="Under", american_odds=100,
            decimal_odds=2.0, book="fanduel",
        ),
    ]
    projs = [
        ProjectedSide(
            market=hr, player_name="Player", line_value=0.5,
            side="Over", model_prob=0.62, confidence=0.55,
            lam=0.7, blend_n=200, blended_rate=0.04,
        ),
        ProjectedSide(
            market=hr, player_name="Player", line_value=0.5,
            side="Under", model_prob=0.38, confidence=0.55,
            lam=0.7, blend_n=200, blended_rate=0.04,
        ),
    ]
    return lines, projs


def test_calibration_demotes_over_confident_picks(synth_hr_pair):
    lines, projs = synth_hr_pair
    raw = build_edge_picks(lines, projs, min_tier=Tier.LEAN,
                           apply_calibration=False)
    cal = build_edge_picks(lines, projs, min_tier=Tier.LEAN,
                           apply_calibration=True)
    # Both runs find the Over.
    raw_over = next(p for p in raw if p.side == "Over")
    cal_over = next(p for p in cal if p.side == "Over")
    assert cal_over.edge_pp < raw_over.edge_pp
    assert cal_over.model_prob < raw_over.model_prob
    # raw_model_prob is preserved on the row regardless of mode.
    assert cal_over.raw_model_prob == pytest.approx(0.62)


def test_min_model_prob_filters_low_conviction(synth_hr_pair):
    lines, projs = synth_hr_pair
    # No floor -> Over survives.
    base = build_edge_picks(lines, projs, min_tier=Tier.LEAN)
    assert any(p.side == "Over" for p in base)
    # Floor at 0.99 -> nothing survives.
    none = build_edge_picks(lines, projs, min_tier=Tier.LEAN,
                            min_model_prob=0.99)
    assert none == []


def test_min_edge_pp_filters_thin_edges(synth_hr_pair):
    lines, projs = synth_hr_pair
    # 50pp is impossible on this synth pair -> empty.
    out = build_edge_picks(lines, projs, min_tier=Tier.LEAN, min_edge_pp=50.0)
    assert out == []


def test_temperature_override_changes_calibrated_prob(synth_hr_pair):
    lines, projs = synth_hr_pair
    # Use NO_PLAY tier floor so the heavy-shrink row isn't filtered by
    # the tier ladder before we can inspect it.
    softer = build_edge_picks(
        lines, projs, min_tier=Tier.NO_PLAY,
        calibration_temperature={"HR": 0.95},  # near-identity
    )
    harder = build_edge_picks(
        lines, projs, min_tier=Tier.NO_PLAY,
        calibration_temperature={"HR": 0.20},  # heavy shrink
    )
    softer_over = next(p for p in softer if p.side == "Over")
    harder_over = next(p for p in harder if p.side == "Over")
    # Heavier shrink -> lower model_prob / smaller edge.
    assert harder_over.model_prob < softer_over.model_prob


def test_default_temperature_dict_covers_all_markets():
    # Every market the engine knows about should have a tau.
    for market in MLB_PROP_MARKETS:
        assert market in DEFAULT_PROPS_TEMPERATURE
        tau = DEFAULT_PROPS_TEMPERATURE[market]
        assert 0.0 < tau <= 1.0
