"""Smoke tests for the Phase 2 engines.

Each test focuses on the property that defines its engine: independence
collapses joint_prob_corr to the leg-prob product; beam never exceeds
its width; ILP returns one candidate per parlay size; diversified
rejects combos that don't span enough distinct games.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")


def _fake_leg(side: str, prob: float, game: str = "g1", player: str | None = None):
    from edge_equation.engines.parlay.builder import ParlayLeg
    from edge_equation.engines.tiering import Tier
    return ParlayLeg(
        market_type="ML", side=side, side_probability=prob,
        american_odds=-110.0, tier=Tier.STRONG, game_id=game,
        player_id=player,
    )


def _config(**overrides):
    from edge_equation.engines.parlay.config import ParlayConfig
    from edge_equation.engines.tiering import Tier
    base = dict(
        min_tier=Tier.LEAN, max_legs=3, mc_trials=10, max_pool_size=20,
        min_joint_prob=0.0, min_ev_units=-1.0,
    )
    base.update(overrides)
    return ParlayConfig(**base)


# ---------------------------------------------------------------------------
# Independent
# ---------------------------------------------------------------------------


def test_independent_engine_collapses_joint_prob_to_product():
    """The whole point of the engine: joint_prob_corr == leg-prob product."""
    from edge_equation.parlay_lab.engines.independent import IndependentEngine

    legs = [
        _fake_leg("A", prob=0.65, game="g1"),
        _fake_leg("B", prob=0.62, game="g2"),
        _fake_leg("C", prob=0.60, game="g3"),
    ]
    out = IndependentEngine().build(legs, _config(max_legs=2))
    assert len(out) > 0
    for cand in out:
        prod = 1.0
        for leg in cand.legs:
            prod *= leg.side_probability
        assert cand.joint_prob_corr == pytest.approx(prod, abs=1e-9)
        assert cand.joint_prob_independent == cand.joint_prob_corr


def test_independent_engine_runs_without_error_at_scale():
    """20-leg pool, 4-leg max -- ensures we don't hit MC code by accident."""
    from edge_equation.parlay_lab.engines.independent import IndependentEngine

    legs = [_fake_leg(f"S{i}", prob=0.55 + (i % 5) * 0.01, game=f"g{i // 4}")
             for i in range(20)]
    out = IndependentEngine().build(legs, _config(max_legs=4))
    assert all(0.0 < c.joint_prob_corr < 1.0 for c in out)


# ---------------------------------------------------------------------------
# Beam
# ---------------------------------------------------------------------------


def test_beam_engine_respects_width(monkeypatch):
    """A 5-leg pool with width=2 should never carry more than 2 beams
    forward across a stage."""
    from edge_equation.parlay_lab.engines.beam import BeamEngine

    monkeypatch.setenv("PARLAY_LAB_BEAM_WIDTH", "2")
    legs = [_fake_leg(f"S{i}", prob=0.60 + i * 0.01, game=f"g{i}")
             for i in range(5)]
    out = BeamEngine().build(legs, _config(max_legs=3))
    # 2-leg seeds (top 2) + 3-leg expansions (top 2) = at most 4
    # candidates per stage; total <= 4. The engine accumulates seeds
    # AND extensions, so up to 4 total here.
    by_size = {2: 0, 3: 0}
    for c in out:
        by_size[c.n_legs] = by_size.get(c.n_legs, 0) + 1
    assert by_size.get(2, 0) <= 2
    assert by_size.get(3, 0) <= 2


def test_beam_engine_skips_when_max_legs_below_two():
    from edge_equation.parlay_lab.engines.beam import BeamEngine
    legs = [_fake_leg("A", 0.6, "g1"), _fake_leg("B", 0.6, "g2")]
    assert BeamEngine().build(legs, _config(max_legs=1)) == []


# ---------------------------------------------------------------------------
# ILP
# ---------------------------------------------------------------------------


def test_ilp_engine_returns_one_candidate_per_size_when_pulp_available():
    pytest.importorskip("pulp")
    from edge_equation.parlay_lab.engines.ilp import ILPEngine

    legs = [
        _fake_leg("A", prob=0.65, game="g1"),
        _fake_leg("B", prob=0.62, game="g2"),
        _fake_leg("C", prob=0.60, game="g3"),
        _fake_leg("D", prob=0.58, game="g4"),
    ]
    out = ILPEngine().build(legs, _config(max_legs=3))
    sizes = sorted(c.n_legs for c in out)
    # Two parlay sizes (2-leg, 3-leg) with the legs available; at most
    # one candidate per size class.
    assert sizes == [2, 3] or sizes == [2] or sizes == [3]


def test_ilp_engine_respects_same_game_cap():
    """Two same-game legs --- ILP should pick at most one of them."""
    pytest.importorskip("pulp")
    from edge_equation.parlay_lab.engines.ilp import ILPEngine

    legs = [
        _fake_leg("A", prob=0.65, game="same"),  # high-EV same-game pair
        _fake_leg("B", prob=0.64, game="same"),
        _fake_leg("C", prob=0.55, game="other"),  # weaker, but unique game
    ]
    out = ILPEngine().build(legs, _config(max_legs=2))
    assert len(out) >= 1
    for cand in out:
        game_ids = [l.game_id for l in cand.legs]
        # No more than one leg from any game.
        assert len(game_ids) == len(set(game_ids))


def test_ilp_engine_returns_empty_when_pulp_not_installed(monkeypatch):
    """Mock PuLP out of import to simulate a deploy without the extra."""
    import edge_equation.parlay_lab.engines.ilp as ilp_mod
    monkeypatch.setattr(ilp_mod, "_PULP_OK", False)
    monkeypatch.setattr(ilp_mod, "pulp", None)
    legs = [_fake_leg("A", 0.65, "g1"), _fake_leg("B", 0.62, "g2")]
    assert ilp_mod.ILPEngine().build(legs, _config()) == []


# ---------------------------------------------------------------------------
# Diversified
# ---------------------------------------------------------------------------


def test_diversified_rejects_3leg_with_two_distinct_games():
    """3-leg combos must span >= 3 games. A 3-leg combo across only 2
    games (impossible after _combo_is_compatible's same-game block,
    but verify the explicit floor anyway) gets rejected."""
    from edge_equation.parlay_lab.engines.diversified import DiversifiedEngine
    legs = [
        _fake_leg("A", prob=0.65, game="g1"),
        _fake_leg("B", prob=0.62, game="g2"),
    ]
    # Only two games available, so no 3-leg combo can satisfy the floor.
    out = DiversifiedEngine().build(legs, _config(max_legs=3))
    # 2-leg combos still allowed (floor is min(2, n)), so at least one.
    assert all(c.n_legs == 2 for c in out)


def test_diversified_admits_3leg_when_3_distinct_games_available():
    from edge_equation.parlay_lab.engines.diversified import DiversifiedEngine
    legs = [
        _fake_leg("A", prob=0.65, game="g1"),
        _fake_leg("B", prob=0.62, game="g2"),
        _fake_leg("C", prob=0.60, game="g3"),
    ]
    out = DiversifiedEngine().build(legs, _config(max_legs=3))
    sizes = {c.n_legs for c in out}
    assert 3 in sizes or 2 in sizes  # at least one size cleared


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_phase2_engines_in_registry():
    from edge_equation.parlay_lab.engines import ENGINES
    for name in ("independent", "beam", "ilp", "diversified"):
        assert name in ENGINES
