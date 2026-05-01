"""Tests for the props sanity gate.

Verifies the calibration-metric math, the gate verdicts vs market /
league-prior baselines, the push handling for actual==line rows, and
the small-sample auto-fail when fewer than ``min_n`` picks are
available.
"""

from __future__ import annotations

from edge_equation.engines.props_prizepicks.evaluation.sanity import (
    GateScores,
    SanityReport,
    _did_side_hit,
    _league_prior_prob,
    _score,
    evaluate_sanity,
)


# ---------------------------------------------------------------------------
# Side-hit / push handling
# ---------------------------------------------------------------------------


def test_did_side_hit_over_wins_when_actual_above_line():
    assert _did_side_hit(2.0, 1.5, "Over") == 1
    assert _did_side_hit(0.0, 0.5, "Over") == 0


def test_did_side_hit_under_wins_when_actual_below_line():
    assert _did_side_hit(0.0, 1.5, "Under") == 1
    assert _did_side_hit(3.0, 1.5, "Under") == 0


def test_did_side_hit_returns_none_on_push():
    """A push (actual == line) should be excluded from the gate sample."""
    assert _did_side_hit(1.5, 1.5, "Over") is None
    assert _did_side_hit(2.0, 2.0, "Under") is None


def test_did_side_hit_is_case_insensitive():
    assert _did_side_hit(2.0, 1.5, "OVER") == 1
    assert _did_side_hit(0.0, 1.5, "under") == 1
    assert _did_side_hit(1.0, 0.5, "Yes") == 1
    assert _did_side_hit(0.0, 0.5, "No") == 1


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def test_score_brier_log_loss_accuracy_match_known_values():
    # Two picks, one perfect (p=0.9, hit) and one wrong (p=0.6, miss).
    s = _score("test", [0.9, 0.6], [1, 0])
    # Brier = ((0.9-1)^2 + (0.6-0)^2) / 2 = (0.01 + 0.36) / 2 = 0.185
    assert abs(s.brier - 0.185) < 1e-6
    # Accuracy: first row correct (0.9>=0.5 & hit), second wrong → 0.5
    assert abs(s.accuracy - 0.5) < 1e-6
    assert s.n == 2


def test_score_handles_empty_input():
    s = _score("empty", [], [])
    assert s.n == 0
    assert s.brier == 0.0


# ---------------------------------------------------------------------------
# League-prior probability
# ---------------------------------------------------------------------------


def test_league_prior_prob_uses_canonical_market_keys():
    """HR Over 0.5 with prior 0.030/PA × 4.1 PAs gives a non-trivial λ."""
    p = _league_prior_prob(market_type="HR", line_value=0.5, side="Over")
    assert 0.0 < p < 1.0
    # P(Over 0.5) = P(X >= 1) for Poisson — should be modest at λ ~0.123.
    assert 0.05 < p < 0.20


def test_league_prior_prob_unknown_market_returns_50_50():
    p = _league_prior_prob(market_type="bogus", line_value=1.5, side="Over")
    assert p == 0.5


def test_league_prior_prob_under_is_complement_of_over():
    p_over = _league_prior_prob(market_type="Hits", line_value=1.5, side="Over")
    p_under = _league_prior_prob(market_type="Hits", line_value=1.5, side="Under")
    assert abs((p_over + p_under) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# evaluate_sanity — top-level gate
# ---------------------------------------------------------------------------


def _row(*, market_type="Hits", line=1.5, side="Over",
            model=0.55, market=0.50, actual=2.0):
    return {
        "market_type":  market_type,
        "line_value":   line,
        "side":         side,
        "model_prob":   model,
        "market_prob":  market,
        "actual_value": actual,
    }


def test_evaluate_sanity_no_rows_returns_empty_report_with_note():
    rep = evaluate_sanity([])
    assert rep.n_picks == 0
    assert rep.gate_passed is False
    assert any("no settled" in n for n in rep.notes)


def test_evaluate_sanity_below_min_n_auto_fails():
    """Even when the model crushes the market, <min_n means we don't claim a verdict."""
    rows = [_row(model=0.95, market=0.50, actual=2.0) for _ in range(5)]
    rep = evaluate_sanity(rows, min_n=50)
    assert rep.n_picks == 5
    assert rep.primary_gate_passed is False
    assert rep.secondary_gate_passed is False
    # Should explicitly note inconclusiveness.
    assert any("settled picks" in n for n in rep.notes)


def test_evaluate_sanity_passes_both_gates_when_model_is_well_calibrated():
    """Model probs match outcomes; market probs are deliberately mis-calibrated."""
    rows = []
    # 60 winners where the model says 0.80 and the market says 0.50.
    for _ in range(60):
        rows.append(_row(market_type="Hits", line=1.5, side="Over",
                          model=0.80, market=0.50, actual=3.0))
    # 40 losers where the model says 0.20 and the market says 0.50.
    for _ in range(40):
        rows.append(_row(market_type="Hits", line=1.5, side="Over",
                          model=0.20, market=0.50, actual=0.0))
    rep = evaluate_sanity(rows, min_n=50)
    assert rep.n_picks == 100
    assert rep.model is not None and rep.market is not None
    assert rep.model.brier < rep.market.brier
    assert rep.primary_gate_passed is True
    assert rep.secondary_gate_passed is True
    assert rep.gate_passed is True


def test_evaluate_sanity_fails_primary_when_model_matches_market():
    """If model == market the primary gate must fail (no skill demonstrated)."""
    rows = []
    for _ in range(60):
        rows.append(_row(model=0.55, market=0.55, actual=3.0))
    for _ in range(40):
        rows.append(_row(model=0.55, market=0.55, actual=0.0))
    rep = evaluate_sanity(rows, min_n=50)
    # Brier(model) and Brier(market) are equal → strict inequality fails.
    assert rep.primary_gate_passed is False


def test_evaluate_sanity_skips_pushes():
    """Pushes (actual==line) should drop out of the sample, not crash."""
    rows = []
    for _ in range(60):
        rows.append(_row(actual=1.5))   # equals line → push
    rep = evaluate_sanity(rows, min_n=10)
    assert rep.n_picks == 0


def test_evaluate_sanity_to_dict_serializes_metrics():
    rows = [_row(model=0.6, market=0.5, actual=2.0) for _ in range(60)]
    rep = evaluate_sanity(rows, min_n=50)
    d = rep.to_dict()
    assert d["n_picks"] == 60
    assert d["model"] is not None
    assert "brier" in d["model"]
    assert d["model"]["n"] == 60
    assert "primary_gate_passed" in d


def test_render_text_includes_pass_fail_lines():
    rows = [_row(model=0.55, market=0.55, actual=2.0) for _ in range(60)]
    rep = evaluate_sanity(rows, min_n=50)
    text = rep.render()
    assert "Primary gate" in text
    assert "Secondary gate" in text
    assert "PASS" in text or "FAIL" in text
