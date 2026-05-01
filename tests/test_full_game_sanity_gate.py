"""Tests for the full-game sanity gate.

Verifies the per-market hit logic, the league-prior re-projection
baseline, the calibration metric math, and the gate verdicts vs
market / league-prior.
"""

from __future__ import annotations

from edge_equation.engines.full_game.config import ProjectionKnobs
from edge_equation.engines.full_game.evaluation.sanity import (
    GateScores,
    SanityReport,
    _did_side_hit,
    _league_prior_prob,
    _score,
    evaluate_sanity,
)


# ---------------------------------------------------------------------------
# _did_side_hit — per-market settlement semantics
# ---------------------------------------------------------------------------


def test_did_side_hit_total_over_wins_when_total_above_line():
    row = dict(market_type="Total", side="Over", team_tricode="",
                home_tricode="NYY", line_value=8.5,
                actual_home=5, actual_away=4)
    assert _did_side_hit(row) == 1


def test_did_side_hit_total_under_wins_when_total_below_line():
    row = dict(market_type="Total", side="Under", team_tricode="",
                home_tricode="NYY", line_value=8.5,
                actual_home=2, actual_away=3)
    assert _did_side_hit(row) == 1


def test_did_side_hit_total_push_returns_none():
    row = dict(market_type="Total", side="Over", team_tricode="",
                home_tricode="NYY", line_value=8.0,
                actual_home=4, actual_away=4)
    assert _did_side_hit(row) is None


def test_did_side_hit_ml_home_wins():
    row = dict(market_type="ML", side="NYY", team_tricode="NYY",
                home_tricode="NYY", line_value=None,
                actual_home=5, actual_away=4)
    assert _did_side_hit(row) == 1


def test_did_side_hit_ml_away_wins():
    row = dict(market_type="ML", side="BOS", team_tricode="BOS",
                home_tricode="NYY", line_value=None,
                actual_home=4, actual_away=5)
    assert _did_side_hit(row) == 1


def test_did_side_hit_ml_loss_when_other_team_wins():
    row = dict(market_type="ML", side="BOS", team_tricode="BOS",
                home_tricode="NYY", line_value=None,
                actual_home=5, actual_away=4)
    assert _did_side_hit(row) == 0


def test_did_side_hit_run_line_favorite_covers():
    """Home -1.5 covers when home wins by 2+."""
    row = dict(market_type="Run_Line", side="NYY", team_tricode="NYY",
                home_tricode="NYY", line_value=-1.5,
                actual_home=5, actual_away=2)
    assert _did_side_hit(row) == 1


def test_did_side_hit_run_line_dog_covers_on_loss_by_one():
    """Home +1.5 covers when home loses by 1 (margin -1 > -1.5)."""
    row = dict(market_type="Run_Line", side="NYY", team_tricode="NYY",
                home_tricode="NYY", line_value=1.5,
                actual_home=3, actual_away=4)
    assert _did_side_hit(row) == 1


def test_did_side_hit_team_total_uses_correct_team():
    row = dict(market_type="Team_Total", side="Over", team_tricode="BOS",
                home_tricode="NYY", line_value=4.5,
                actual_home=2, actual_away=6)
    assert _did_side_hit(row) == 1


def test_did_side_hit_f5_total_uses_f5_columns():
    row = dict(market_type="F5_Total", side="Over", team_tricode="",
                home_tricode="NYY", line_value=4.5,
                actual_home=8, actual_away=7,
                f5_home_runs=3, f5_away_runs=3)
    assert _did_side_hit(row) == 1


def test_did_side_hit_f5_total_missing_f5_data_returns_none():
    row = dict(market_type="F5_Total", side="Over", team_tricode="",
                home_tricode="NYY", line_value=4.5,
                actual_home=8, actual_away=7,
                f5_home_runs=None, f5_away_runs=None)
    assert _did_side_hit(row) is None


def test_did_side_hit_unknown_market_returns_none():
    row = dict(market_type="WeirdMarket", side="Over", team_tricode="",
                home_tricode="NYY", line_value=8.5,
                actual_home=5, actual_away=4)
    assert _did_side_hit(row) is None


# ---------------------------------------------------------------------------
# League-prior probability
# ---------------------------------------------------------------------------


def test_league_prior_total_uses_league_lambda():
    """Total Over 8.5 with λ=2*4.55*1.03+(no HFA on away)... is non-trivial."""
    knobs = ProjectionKnobs()
    p = _league_prior_prob(
        dict(market_type="Total", side="Over", team_tricode="",
              home_tricode="NYY", line_value=8.5),
        knobs=knobs,
    )
    assert p is not None and 0.0 < p < 1.0
    # Around λ_total ≈ 9.24 (with HFA), P(Over 8.5) should be > 0.45
    assert 0.40 < p < 0.65


def test_league_prior_ml_with_hfa_favors_home_directionally():
    """HFA bump should make home P(win) strictly greater than away P(win).

    Note: Skellam P(margin > 0) is the strict ``home_runs > away_runs``
    probability, so under symmetric λs both sides sit < 0.5 (the
    remainder is the tie probability). MLB regular-season games can't
    tie so production accounting redistributes ties at settle time;
    the gate's league-prior just uses the raw Skellam tail, matching
    the projection module's own behavior.
    """
    knobs = ProjectionKnobs()
    p_home = _league_prior_prob(
        dict(market_type="ML", side="NYY", team_tricode="NYY",
              home_tricode="NYY", line_value=None),
        knobs=knobs,
    )
    p_away = _league_prior_prob(
        dict(market_type="ML", side="BOS", team_tricode="BOS",
              home_tricode="NYY", line_value=None),
        knobs=knobs,
    )
    assert p_home > p_away


def test_league_prior_unknown_market_returns_none():
    knobs = ProjectionKnobs()
    p = _league_prior_prob(
        dict(market_type="WeirdMarket", side="Over", team_tricode="",
              home_tricode="NYY", line_value=8.5),
        knobs=knobs,
    )
    assert p is None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_score_brier_log_loss_accuracy_match_known_values():
    s = _score("test", [0.9, 0.6], [1, 0])
    assert abs(s.brier - 0.185) < 1e-6
    assert abs(s.accuracy - 0.5) < 1e-6
    assert s.n == 2


# ---------------------------------------------------------------------------
# evaluate_sanity — top-level gate
# ---------------------------------------------------------------------------


def _row(*, market_type="Total", side="Over", team_tricode="",
            home_tricode="NYY", line_value=8.5,
            model_prob=0.55, market_prob=0.50,
            actual_home=5, actual_away=4,
            f5_home_runs=None, f5_away_runs=None):
    return dict(
        market_type=market_type, side=side, team_tricode=team_tricode,
        home_tricode=home_tricode, line_value=line_value,
        model_prob=model_prob, market_prob=market_prob,
        actual_home=actual_home, actual_away=actual_away,
        f5_home_runs=f5_home_runs, f5_away_runs=f5_away_runs,
    )


def test_evaluate_sanity_no_rows_returns_empty_report():
    rep = evaluate_sanity([])
    assert rep.n_picks == 0
    assert rep.gate_passed is False


def test_evaluate_sanity_below_min_n_auto_fails():
    rows = [_row(model_prob=0.95) for _ in range(5)]
    rep = evaluate_sanity(rows, min_n=50)
    assert rep.n_picks == 5
    assert rep.primary_gate_passed is False
    assert rep.secondary_gate_passed is False


def test_evaluate_sanity_passes_both_gates_when_well_calibrated():
    rows = []
    # 60 winners with confident model.
    for _ in range(60):
        rows.append(_row(model_prob=0.80, market_prob=0.50,
                          actual_home=6, actual_away=4))
    # 40 losers with confident model.
    for _ in range(40):
        rows.append(_row(model_prob=0.20, market_prob=0.50,
                          actual_home=2, actual_away=3))
    rep = evaluate_sanity(rows, min_n=50)
    assert rep.n_picks == 100
    assert rep.model.brier < rep.market.brier
    assert rep.primary_gate_passed is True
    assert rep.secondary_gate_passed is True
    assert rep.gate_passed is True


def test_evaluate_sanity_fails_primary_when_model_matches_market():
    rows = [_row(model_prob=0.55, market_prob=0.55,
                  actual_home=5, actual_away=4) for _ in range(60)]
    rep = evaluate_sanity(rows, min_n=50)
    assert rep.primary_gate_passed is False


def test_evaluate_sanity_to_dict_serializes_metrics():
    rows = [_row(model_prob=0.6, market_prob=0.5,
                  actual_home=5, actual_away=4) for _ in range(60)]
    rep = evaluate_sanity(rows, min_n=50)
    d = rep.to_dict()
    assert d["n_picks"] == 60
    assert d["model"]["n"] == 60
    assert "primary_gate_passed" in d


def test_render_text_includes_pass_fail_lines():
    rows = [_row(model_prob=0.55, market_prob=0.55,
                  actual_home=5, actual_away=4) for _ in range(60)]
    rep = evaluate_sanity(rows, min_n=50)
    text = rep.render()
    assert "Full-Game sanity gate" in text
    assert "Primary gate" in text
    assert "Secondary gate" in text
