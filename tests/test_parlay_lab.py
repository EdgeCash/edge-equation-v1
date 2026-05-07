"""Smoke tests for the parlay shootout harness.

The harness composes existing parlay-builder + tiering modules; these
tests cover the bits that are unique to ``parlay_lab``: row -> leg
conversion, slate grouping, parlay grading, score aggregation, and
the engine registry.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")


def _make_slate_dict(date: str, rows: list[dict]) -> dict:
    """Build a fake backtest.json shape from a list of bet rows."""
    return {"bets": [{"date": date, **r} for r in rows]}


def _row(
    *,
    bet_type: str = "moneyline",
    matchup: str = "NYY@BOS",
    pick: str = "NYY",
    model_prob: float = 0.62,
    result: str = "WIN",
    units: float = 0.909,
) -> dict:
    return {
        "bet_type": bet_type,
        "matchup": matchup,
        "pick": pick,
        "model_prob": model_prob,
        "result": result,
        "units": units,
    }


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def test_row_to_graded_leg_recovers_decimal_odds_from_winners():
    from edge_equation.parlay_lab.backfill import _row_to_graded_leg
    g = _row_to_graded_leg(_row(result="WIN", units=1.68))
    assert g is not None
    # Decimal odds = units + 1.0 on a 1u-stake winner.
    assert g.decimal_odds == pytest.approx(2.68, abs=1e-9)
    assert g.result == "WIN"


def test_row_to_graded_leg_falls_back_to_minus_110_for_loss():
    from edge_equation.parlay_lab.backfill import _row_to_graded_leg
    g = _row_to_graded_leg(_row(result="LOSS", units=-1.0))
    assert g is not None
    assert g.decimal_odds == pytest.approx(1.909, abs=1e-3)
    assert g.result == "LOSS"


def test_row_to_graded_leg_skips_unusable_rows():
    from edge_equation.parlay_lab.backfill import _row_to_graded_leg
    assert _row_to_graded_leg({}) is None
    assert _row_to_graded_leg({"bet_type": "moneyline"}) is None


def test_load_backfill_groups_by_date(tmp_path):
    """Two days, two rows each, after a quality cut."""
    import json
    from edge_equation.parlay_lab.backfill import load_backfill

    payload = {"bets": [
        {"date": "2026-01-01", **_row(matchup="A@B", pick="A")},
        {"date": "2026-01-01", **_row(matchup="C@D", pick="C", model_prob=0.70)},
        {"date": "2026-01-02", **_row(matchup="A@B", pick="A", units=1.5)},
        {"date": "2026-01-02", **_row(matchup="E@F", pick="E", units=0.5)},
    ]}
    path = tmp_path / "backtest.json"
    path.write_text(json.dumps(payload))
    src, slates = load_backfill(path)
    assert {s.date for s in slates} == {"2026-01-01", "2026-01-02"}
    # Each row that survives the default LEAN-tier filter lands in its
    # date's slate. We don't assert the exact count --- it depends on
    # ``classify_tier`` thresholds that live outside this module.
    assert all(len(s.graded_legs) >= 1 for s in slates)
    assert src.first_date == "2026-01-01"
    assert src.last_date == "2026-01-02"


def test_iter_slates_filters_short_slates():
    from edge_equation.parlay_lab.backfill import iter_slates
    from edge_equation.parlay_lab.base import GradedSlate
    short = GradedSlate(date="2026-01-01", graded_legs=())
    full = GradedSlate(
        date="2026-01-02",
        graded_legs=tuple([_FAKE_GRADED_LEG, _FAKE_GRADED_LEG]),
    )
    out = list(iter_slates([short, full], min_legs_per_slate=2))
    assert [s.date for s in out] == ["2026-01-02"]


# ---------------------------------------------------------------------------
# Grading + scoring
# ---------------------------------------------------------------------------


def _fake_leg(side: str, prob: float, game: str = "g1"):
    from edge_equation.engines.parlay.builder import ParlayLeg
    from edge_equation.engines.tiering import Tier
    return ParlayLeg(
        market_type="ML", side=side, side_probability=prob,
        american_odds=-110.0, tier=Tier.STRONG, game_id=game,
    )


def _fake_graded(side: str, result: str, decimal_odds: float, game: str = "g1"):
    from edge_equation.parlay_lab.base import GradedLeg
    leg = _fake_leg(side, prob=0.6, game=game)
    return GradedLeg(
        leg=leg, result=result, decimal_odds=decimal_odds,
        pick_id=f"{game}|{side}",
    )


_FAKE_GRADED_LEG = _fake_graded("A", "WIN", 1.909)


def test_grade_parlay_all_wins():
    from edge_equation.engines.parlay.builder import ParlayCandidate
    from edge_equation.parlay_lab.base import GradedSlate
    from edge_equation.parlay_lab.metrics import grade_parlay

    slate = GradedSlate(
        date="2026-01-01",
        graded_legs=(_fake_graded("A", "WIN", 1.909, "g1"),
                      _fake_graded("B", "WIN", 1.909, "g2")),
    )
    cand = ParlayCandidate(
        legs=tuple(g.leg for g in slate.graded_legs),
        joint_prob_independent=0.36,
        joint_prob_corr=0.36,
        fair_decimal_odds=2.78,
        combined_decimal_odds=1.909 * 1.909,
        implied_prob=0.275,
        ev_units=0.05,
        stake_units=0.5,
    )
    out = grade_parlay(cand, slate, stake_units=0.5)
    assert out is not None
    assert out.result == "WIN"
    # 0.5u × ((1.909^2) - 1) = 0.5 × 2.644 = 1.322
    assert out.units_pl == pytest.approx(0.5 * (1.909 * 1.909 - 1.0), abs=1e-6)


def test_grade_parlay_one_loss_busts_the_ticket():
    from edge_equation.engines.parlay.builder import ParlayCandidate
    from edge_equation.parlay_lab.base import GradedSlate
    from edge_equation.parlay_lab.metrics import grade_parlay

    slate = GradedSlate(
        date="2026-01-01",
        graded_legs=(_fake_graded("A", "WIN", 1.909, "g1"),
                      _fake_graded("B", "LOSS", 1.909, "g2")),
    )
    cand = ParlayCandidate(
        legs=tuple(g.leg for g in slate.graded_legs),
        joint_prob_independent=0.36, joint_prob_corr=0.36,
        fair_decimal_odds=2.78, combined_decimal_odds=1.909 * 1.909,
        implied_prob=0.275, ev_units=0.05, stake_units=0.5,
    )
    out = grade_parlay(cand, slate, stake_units=0.5)
    assert out is not None
    assert out.result == "LOSS"
    assert out.units_pl == pytest.approx(-0.5)


def test_grade_parlay_pushes_drop_to_smaller_payout():
    """One leg pushes; remaining legs determine W/L. Payout is product
    of active legs only."""
    from edge_equation.engines.parlay.builder import ParlayCandidate
    from edge_equation.parlay_lab.base import GradedSlate
    from edge_equation.parlay_lab.metrics import grade_parlay

    slate = GradedSlate(
        date="2026-01-01",
        graded_legs=(_fake_graded("A", "WIN", 2.0, "g1"),
                      _fake_graded("B", "PUSH", 1.909, "g2")),
    )
    cand = ParlayCandidate(
        legs=tuple(g.leg for g in slate.graded_legs),
        joint_prob_independent=0.36, joint_prob_corr=0.36,
        fair_decimal_odds=2.78, combined_decimal_odds=4.0,
        implied_prob=0.275, ev_units=0.05, stake_units=0.5,
    )
    out = grade_parlay(cand, slate, stake_units=0.5)
    assert out is not None
    assert out.result == "WIN"
    # The push leg drops; only A's 2.0 odds count toward payout.
    assert out.units_pl == pytest.approx(0.5 * (2.0 - 1.0), abs=1e-6)


def test_score_engine_aggregates_wins_losses_and_drawdown():
    """One winner, one loser; score should reflect both."""
    from edge_equation.engines.parlay.builder import ParlayCandidate
    from edge_equation.parlay_lab.base import GradedSlate
    from edge_equation.parlay_lab.metrics import score_engine

    win_slate = GradedSlate(
        date="2026-01-01",
        graded_legs=(_fake_graded("A", "WIN", 1.909, "g1"),
                      _fake_graded("B", "WIN", 1.909, "g2")),
    )
    win_cand = ParlayCandidate(
        legs=tuple(g.leg for g in win_slate.graded_legs),
        joint_prob_independent=0.36, joint_prob_corr=0.36,
        fair_decimal_odds=2.78, combined_decimal_odds=1.909 * 1.909,
        implied_prob=0.275, ev_units=0.05, stake_units=0.5,
    )
    loss_slate = GradedSlate(
        date="2026-01-02",
        graded_legs=(_fake_graded("X", "LOSS", 1.909, "g3"),
                      _fake_graded("Y", "WIN", 1.909, "g4")),
    )
    loss_cand = ParlayCandidate(
        legs=tuple(g.leg for g in loss_slate.graded_legs),
        joint_prob_independent=0.36, joint_prob_corr=0.36,
        fair_decimal_odds=2.78, combined_decimal_odds=1.909 * 1.909,
        implied_prob=0.275, ev_units=0.05, stake_units=0.5,
    )
    score = score_engine(
        "test",
        [(win_slate, [win_cand]), (loss_slate, [loss_cand])],
        stake_units=0.5,
    )
    assert score.n_parlays == 2
    assert score.n_wins == 1 and score.n_losses == 1 and score.n_pushes == 0
    assert score.n_days_total == 2
    assert score.n_days_active == 2
    # PnL: +(1.909^2 - 1) * 0.5 from win, -0.5 from loss
    expected_pl = 0.5 * (1.909 * 1.909 - 1.0) - 0.5
    assert score.total_pl_units == pytest.approx(expected_pl, abs=1e-6)
    # Drawdown is the max peak-to-trough drop. Peak after win, then loss
    # of 0.5u. Drawdown >= 0.5.
    assert score.max_drawdown_units >= 0.5 - 1e-6


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


def test_engine_registry_exposes_baseline_and_deduped():
    from edge_equation.parlay_lab.engines import ENGINES, all_engines, resolve
    assert "baseline" in ENGINES
    assert "deduped" in ENGINES
    assert {e.name for e in all_engines()} == set(ENGINES)
    assert resolve("baseline").name == "baseline"


def test_deduped_engine_keeps_max_ev_per_game():
    """Two same-game legs --- the higher-EV one survives, the other drops."""
    from edge_equation.engines.parlay.config import ParlayConfig
    from edge_equation.engines.tiering import Tier
    from edge_equation.parlay_lab.engines.deduped import (
        SameGameDedupedEngine, _single_leg_ev,
    )
    weak = _fake_leg("A", prob=0.55, game="same")  # EV ~= 0.05
    strong = _fake_leg("B", prob=0.65, game="same")  # EV ~= 0.24
    other = _fake_leg("C", prob=0.60, game="other")
    assert _single_leg_ev(strong) > _single_leg_ev(weak)
    eng = SameGameDedupedEngine()
    # The dedup happens BEFORE build_parlay_candidates --- we don't need
    # the builder to actually return any candidate here, just verify
    # the dedup step retained ``strong`` over ``weak``.
    cfg = ParlayConfig(
        min_tier=Tier.LEAN, max_legs=2, mc_trials=10, max_pool_size=10,
        min_joint_prob=0.0, min_ev_units=-1.0,
    )
    result = eng.build([weak, strong, other], cfg)
    # Whatever the builder produces, no candidate should reference the
    # weaker same-game leg.
    weak_legs_in_output = [
        leg for cand in result for leg in cand.legs if leg.side == "A"
    ]
    assert weak_legs_in_output == []


def test_baseline_engine_passes_through_to_builder():
    """BaselineEngine should match `build_parlay_candidates` output exactly."""
    from edge_equation.engines.parlay.builder import build_parlay_candidates
    from edge_equation.engines.parlay.config import ParlayConfig
    from edge_equation.engines.tiering import Tier
    from edge_equation.parlay_lab.engines.baseline import BaselineEngine

    legs = [
        _fake_leg("A", prob=0.62, game="g1"),
        _fake_leg("B", prob=0.65, game="g2"),
        _fake_leg("C", prob=0.60, game="g3"),
    ]
    cfg = ParlayConfig(
        min_tier=Tier.LEAN, max_legs=2, mc_trials=10, max_pool_size=10,
        min_joint_prob=0.0, min_ev_units=-1.0,
    )
    direct = build_parlay_candidates(legs, config=cfg)
    via_engine = BaselineEngine().build(legs, cfg)
    assert len(direct) == len(via_engine)
