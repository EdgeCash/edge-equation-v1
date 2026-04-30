"""Tests for the parlay builder + MC joint-probability simulation."""

from __future__ import annotations

import pytest

from edge_equation.engines.parlay import (
    ParlayCandidate, ParlayConfig, ParlayLeg,
    build_parlay_candidates, expected_value_units, qualify_parlay,
    render_candidate, simulate_correlated_joint_prob,
)
from edge_equation.engines.parlay.builder import (
    _decimal_to_american, _build_correlation_matrix,
)
from edge_equation.engines.tiering import Tier


def _leg(market="NRFI", side="Under 0.5", prob=0.80, odds=-115,
          tier=Tier.STRONG, game=None, player=None, label=""):
    return ParlayLeg(
        market_type=market, side=side,
        side_probability=prob, american_odds=odds, tier=tier,
        game_id=game, player_id=player, label=label,
    )


# ---------------------------------------------------------------------------
# Money math
# ---------------------------------------------------------------------------


def test_decimal_to_american_underdog_and_favorite():
    assert _decimal_to_american(2.0)  == pytest.approx(100.0)
    assert _decimal_to_american(2.5)  == pytest.approx(150.0)
    assert _decimal_to_american(1.5)  == pytest.approx(-200.0)


def test_expected_value_units_breakeven():
    """At fair odds (joint = 1/decimal), EV is exactly 0."""
    ev = expected_value_units(joint_prob=0.5, combined_decimal_odds=2.0,
                                stake_units=1.0)
    assert ev == pytest.approx(0.0)


def test_expected_value_units_positive_when_overlay():
    """50% to win at +110 → +0.05u per 1u stake."""
    ev = expected_value_units(joint_prob=0.5, combined_decimal_odds=2.10,
                                stake_units=1.0)
    assert ev == pytest.approx(0.05, abs=1e-9)


# ---------------------------------------------------------------------------
# MC sanity — independence + degenerate edge cases
# ---------------------------------------------------------------------------


def test_simulate_independent_legs_matches_product():
    """Two cross-game legs with no correlation table entry → MC
    estimate within a couple percent of the simple product."""
    legs = [
        _leg(market="NRFI", prob=0.80, game="g1"),
        _leg(market="ML",   prob=0.70, game="g2"),
    ]
    p = simulate_correlated_joint_prob(legs, n_trials=20_000, seed=11)
    assert p == pytest.approx(0.80 * 0.70, abs=0.015)


def test_simulate_three_independent_legs_matches_product():
    legs = [
        _leg(market="NRFI", prob=0.85, game="g1"),
        _leg(market="ML",   prob=0.70, game="g2"),
        _leg(market="HR",   prob=0.60, game="g3", player="p1"),
    ]
    p = simulate_correlated_joint_prob(legs, n_trials=30_000, seed=7)
    assert p == pytest.approx(0.85 * 0.70 * 0.60, abs=0.02)


def test_simulate_single_leg_returns_marginal():
    legs = [_leg(prob=0.42, game="g1")]
    p = simulate_correlated_joint_prob(legs, n_trials=1_000, seed=1)
    assert p == pytest.approx(0.42, abs=1e-9)


def test_simulate_empty_legs_returns_zero():
    p = simulate_correlated_joint_prob([], n_trials=100, seed=1)
    assert p == 0.0


def test_simulate_positive_correlation_lifts_joint_prob_above_product():
    """NRFI + F5_Total Under in the same game (ρ ≈ +0.5). Joint
    probability must exceed the independence product."""
    legs = [
        _leg(market="NRFI",     side="Under 0.5", prob=0.78, game="g1"),
        _leg(market="F5_Total", side="Under 4.5", prob=0.65, game="g1"),
    ]
    p_corr = simulate_correlated_joint_prob(legs, n_trials=40_000, seed=13)
    p_indep = 0.78 * 0.65
    assert p_corr > p_indep + 0.02
    # ... but not by an unreasonable amount.
    assert p_corr < min(0.78, 0.65) + 0.01


def test_simulate_negative_correlation_pushes_joint_below_product():
    """Hits Over + K Yes for the same hitter (ρ ≈ -0.4). Joint must
    be smaller than the independence product."""
    legs = [
        _leg(market="Hits", side="Over 1.5", prob=0.60,
              game="g1", player="p1"),
        _leg(market="K",    side="Yes",     prob=0.55,
              game="g1", player="p1"),
    ]
    p_corr = simulate_correlated_joint_prob(legs, n_trials=40_000, seed=17)
    p_indep = 0.60 * 0.55
    assert p_corr < p_indep - 0.03


def test_simulate_correlation_matrix_clipped():
    """A leg pair with ρ that would clip should still produce a
    Cholesky-decomposable matrix — no NaN return."""
    legs = [
        _leg(market="NRFI", side="Under 0.5", prob=0.7, game="g1"),
        _leg(market="ML",   side="Yankees ML", prob=0.7, game="g1"),
    ]
    p = simulate_correlated_joint_prob(
        legs, n_trials=2_000, seed=1, max_abs_correlation=0.85,
    )
    assert 0.0 < p < 1.0


def test_correlation_matrix_diagonal_is_one():
    legs = [
        _leg(market="NRFI", game="g1"),
        _leg(market="ML",   game="g1"),
    ]
    M = _build_correlation_matrix(legs, max_abs_correlation=0.85)
    assert M[0, 0] == 1.0
    assert M[1, 1] == 1.0
    assert M[0, 1] == M[1, 0]


# ---------------------------------------------------------------------------
# Qualification gates
# ---------------------------------------------------------------------------


def test_qualify_parlay_requires_two_or_more_legs():
    cfg = ParlayConfig()
    legs = [_leg()]
    assert qualify_parlay(legs, joint_prob_corr=0.95, ev_units=1.0,
                            config=cfg) is False


def test_qualify_parlay_rejects_when_above_max_legs():
    cfg = ParlayConfig(max_legs=3)
    legs = [_leg(game=f"g{i}") for i in range(4)]
    assert qualify_parlay(legs, joint_prob_corr=0.95, ev_units=1.0,
                            config=cfg) is False


def test_qualify_parlay_rejects_lean_or_moderate_legs():
    cfg = ParlayConfig()
    legs = [_leg(tier=Tier.STRONG, game="g1"),
              _leg(tier=Tier.MODERATE, game="g2")]
    assert qualify_parlay(legs, joint_prob_corr=0.95, ev_units=1.0,
                            config=cfg) is False


def test_qualify_parlay_accepts_lock_and_strong_combinations():
    cfg = ParlayConfig()
    legs = [_leg(tier=Tier.ELITE, game="g1"),
              _leg(tier=Tier.STRONG, game="g2")]
    assert qualify_parlay(legs, joint_prob_corr=0.75, ev_units=0.30,
                            config=cfg) is True


def test_qualify_parlay_enforces_min_joint_prob():
    cfg = ParlayConfig(min_joint_prob=0.68)
    legs = [_leg(tier=Tier.STRONG, game="g1"),
              _leg(tier=Tier.STRONG, game="g2")]
    assert qualify_parlay(legs, joint_prob_corr=0.65, ev_units=1.0,
                            config=cfg) is False


def test_qualify_parlay_enforces_min_ev_units():
    cfg = ParlayConfig(min_ev_units=0.25)
    legs = [_leg(tier=Tier.STRONG, game="g1"),
              _leg(tier=Tier.STRONG, game="g2")]
    assert qualify_parlay(legs, joint_prob_corr=0.95, ev_units=0.10,
                            config=cfg) is False


# ---------------------------------------------------------------------------
# Builder pipeline
# ---------------------------------------------------------------------------


def test_builder_returns_no_candidates_for_low_prob_pool():
    """A pool of 60% legs should produce zero candidates against the
    audit-strict 0.68 joint floor."""
    legs = [
        _leg(prob=0.60, tier=Tier.STRONG, game=f"g{i}")
        for i in range(4)
    ]
    cands = build_parlay_candidates(legs)
    assert cands == []


def test_builder_returns_candidate_for_two_lock_nrfis():
    """Two LOCK NRFIs in different games at -110 → easy 70%+ joint
    and large EV. Classic Special Drop."""
    legs = [
        _leg(market="NRFI", prob=0.85, odds=-115,
              tier=Tier.ELITE, game="g1", label="g1 NRFI"),
        _leg(market="NRFI", prob=0.84, odds=-110,
              tier=Tier.ELITE, game="g2", label="g2 NRFI"),
    ]
    cands = build_parlay_candidates(legs)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.n_legs == 2
    assert cand.joint_prob_corr >= 0.68
    assert cand.ev_units >= 0.25


def test_builder_filters_legs_below_min_tier():
    legs = [
        _leg(prob=0.85, tier=Tier.LEAN,     game="g1"),
        _leg(prob=0.85, tier=Tier.MODERATE, game="g2"),
        _leg(prob=0.85, tier=Tier.STRONG,   game="g3"),
    ]
    # Only one STRONG-or-better leg in pool → no 2-leg combo possible.
    assert build_parlay_candidates(legs) == []


def test_builder_excludes_mutually_exclusive_pairs():
    """NRFI + YRFI on the same game must never appear in a candidate."""
    legs = [
        _leg(market="NRFI", prob=0.85, tier=Tier.ELITE, game="g1"),
        _leg(market="YRFI", prob=0.84, tier=Tier.ELITE, game="g1"),
    ]
    cands = build_parlay_candidates(legs)
    assert cands == []


def test_builder_excludes_same_market_same_game_different_sides():
    """E.g., Yankees ML + Red Sox ML on the same game — the two sides
    of one moneyline can't both win."""
    legs = [
        _leg(market="ML", side="Yankees ML",
              prob=0.75, tier=Tier.STRONG, game="g1"),
        _leg(market="ML", side="Red Sox ML",
              prob=0.70, tier=Tier.STRONG, game="g1"),
    ]
    cands = build_parlay_candidates(legs)
    assert cands == []


def test_builder_sorts_by_ev_descending():
    """Two qualifying combos — the higher-EV one comes first."""
    legs = [
        _leg(market="NRFI", prob=0.95, odds=-110,
              tier=Tier.ELITE, game="g1"),
        _leg(market="NRFI", prob=0.90, odds=-110,
              tier=Tier.ELITE, game="g2"),
        _leg(market="NRFI", prob=0.85, odds=-110,
              tier=Tier.ELITE, game="g3"),
    ]
    cands = build_parlay_candidates(legs)
    assert len(cands) >= 2
    evs = [c.ev_units for c in cands]
    assert evs == sorted(evs, reverse=True)


def test_builder_respects_max_legs_config():
    """With max_legs=2, no 3-leg combo should be produced."""
    cfg = ParlayConfig(max_legs=2)
    legs = [
        _leg(market="NRFI", prob=0.92, odds=-115,
              tier=Tier.ELITE, game=f"g{i}")
        for i in range(4)
    ]
    cands = build_parlay_candidates(legs, config=cfg)
    assert all(c.n_legs == 2 for c in cands)


def test_builder_emits_n_choose_k_combos_when_all_qualify():
    """4 strong cross-game legs → C(4,2) + C(4,3) = 10 combos
    (all should pass the gates given strong probabilities)."""
    cfg = ParlayConfig(max_legs=3)
    legs = [
        _leg(market="NRFI", prob=0.90, odds=-110,
              tier=Tier.ELITE, game=f"g{i}")
        for i in range(4)
    ]
    cands = build_parlay_candidates(legs, config=cfg)
    # n=2: C(4,2)=6; n=3: C(4,3)=4 → 10 total. All cross-game so no
    # mutual-exclusion pruning.
    assert len(cands) == 10


# ---------------------------------------------------------------------------
# render_candidate
# ---------------------------------------------------------------------------


def test_render_candidate_includes_legs_tier_and_ev():
    legs = [
        _leg(market="NRFI", side="Under 0.5",
              prob=0.85, odds=-115, tier=Tier.ELITE,
              game="g1", label="Yankees @ Red Sox NRFI"),
        _leg(market="NRFI", side="Under 0.5",
              prob=0.84, odds=-110, tier=Tier.ELITE,
              game="g2", label="Dodgers @ Giants NRFI"),
    ]
    cand = build_parlay_candidates(legs)[0]
    text = render_candidate(cand)
    assert "PARLAY (2 legs)" in text
    assert "ELITE" in text
    assert "Yankees @ Red Sox NRFI" in text
    assert "Dodgers @ Giants NRFI" in text
    assert "joint prob" in text
    assert "EV @" in text
    assert "edge" in text
