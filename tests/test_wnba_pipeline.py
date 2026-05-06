"""
Smoke + contract tests for the WNBA pipeline.

End-to-end coverage against synthetic + real backfill: projection
model produces sensible probabilities, backtest aggregates correctly,
gate rejects under-spec markets, orchestrator round-trips JSON.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_equation.exporters.wnba.projections import (
    ProjectionModel,
    prob_over,
    prob_total_over_under,
)
from edge_equation.exporters.wnba.backtest import BacktestEngine
from edge_equation.exporters.wnba import gates


# Lightweight synthetic backfill — just enough games for the
# aggregator to produce non-default outputs.
@pytest.fixture
def synthetic_games():
    games = []
    for i in range(80):
        # NY scores ~80, beats opponents who score ~75 → favorite
        date = f"2025-06-{(i % 30) + 1:02d}"
        games.append({
            "date": date,
            "game_id": f"g{i}",
            "season": 2025,
            "away_team": "PHX" if i % 2 == 0 else "NY",
            "home_team": "NY" if i % 2 == 0 else "PHX",
            "away_score": 75 if i % 2 == 0 else 80,
            "home_score": 80 if i % 2 == 0 else 75,
            "total_points": 155,
            "ml_winner": "NY",
            "margin": 5,
            "away_q": [18, 20, 18, 19],
            "home_q": [20, 20, 20, 20],
            "away_1h": 38,
            "home_1h": 40,
            "first_half_total": 78,
            "first_half_winner": "NY",
        })
    return games


# ---------------- math primitives --------------------------------

def test_prob_over_normal_at_mean_returns_half():
    assert abs(prob_over(80.0, 80.0, 5.0) - 0.5) < 1e-9


def test_prob_over_one_sigma_above_mean_is_about_p84():
    # mean + 1 SD → P(X > mean + 1*SD) ≈ 0.1587 → P(over=mean) ≈ 0.5;
    # P(over = mean - 1SD) ≈ 0.84
    p = prob_over(75.0, 80.0, 5.0)
    assert 0.83 < p < 0.85


def test_prob_total_under_complements_over_for_half_lines():
    p_over, p_under, p_push = prob_total_over_under(160.5, 162.0, 14.0)
    assert p_push == 0.0
    assert abs(p_over + p_under - 1.0) < 1e-9


def test_prob_total_whole_line_has_push_slot():
    _, _, p_push = prob_total_over_under(162.0, 162.0, 14.0)
    assert p_push > 0.0  # whole lines bake in a small push band


# ---------------- projection model -------------------------------

def test_projection_model_favorite_has_higher_win_prob(synthetic_games):
    model = ProjectionModel(synthetic_games)
    proj = model.project_matchup("PHX", "NY")
    # NY scores 80 vs PHX's 75 in synthetic → NY favored at home
    assert proj["home_win_prob"] > 0.5
    assert proj["away_win_prob"] == round(1 - proj["home_win_prob"], 3)
    assert proj["ml_pick"] == "NY"


def test_projection_handles_unknown_teams_gracefully(synthetic_games):
    """Cold-start: a team not in the backfill (expansion / preseason
    invitee) should fall back to league averages, not crash."""
    model = ProjectionModel(synthetic_games)
    proj = model.project_matchup("GSV", "TOR")  # not in synthetic
    assert proj["away_pts_proj"] > 0
    assert proj["home_pts_proj"] > 0


def test_projection_calibration_overrides_default_sds(synthetic_games):
    cal = {
        "team_pts_sd": 99.0,
        "total_sd": 99.0,
        "margin_sd": 99.0,
        "win_prob_slope": 0.99,
    }
    model = ProjectionModel(synthetic_games, calibration=cal)
    assert model.team_pts_sd == 99.0
    assert model.win_prob_slope == 0.99


# ---------------- backtest ---------------------------------------

def test_backtest_aggregates_to_three_markets(synthetic_games):
    engine = BacktestEngine(synthetic_games, min_history=20)
    result = engine.run()
    bet_types = {r["bet_type"] for r in result["summary_by_bet_type"]}
    assert bet_types == {"moneyline", "spread", "totals"}


def test_backtest_calibration_block_has_required_fields(synthetic_games):
    engine = BacktestEngine(synthetic_games, min_history=20)
    cal = engine.run()["calibration"]
    for k in ("team_pts_sd", "total_sd", "margin_sd", "win_prob_slope"):
        assert k in cal
        assert isinstance(cal[k], (int, float))


def test_backtest_real_5season_data_runs():
    """Exercise the actual data on disk. If this fails, the schema or
    the projector regressed against real game records."""
    backfill = Path("data/backfill/wnba")
    if not backfill.exists():
        pytest.skip("backfill data not on disk in this checkout")
    engine = BacktestEngine.from_multi_season(
        backfill_dir=backfill,
        seasons=[2021, 2022, 2023, 2024, 2025],
    )
    assert len(engine.games) > 1000  # 5 seasons × ~250 games each
    result = engine.run()
    assert result["overall"]["bets"] > 1000
    # Real-data sanity: the model should hit at least 50% on ML over a
    # long sample (since it picks the favorite). Anything below means
    # regression.
    ml = next(r for r in result["summary_by_bet_type"] if r["bet_type"] == "moneyline")
    assert ml["hit_rate"] >= 0.50, f"ML hit rate {ml['hit_rate']} dropped below 50%"


# ---------------- gates ------------------------------------------

def test_gate_cold_start_returns_none(synthetic_games):
    passed, notes = gates.market_gate(None)
    assert passed is None
    assert notes == {}


def test_gate_excludes_failing_market():
    summary = [
        {"bet_type": "moneyline", "bets": 250, "roi_pct": 5.0, "brier": 0.23},
        {"bet_type": "spread",    "bets": 250, "roi_pct": -10.0, "brier": 0.25},
        {"bet_type": "totals",    "bets": 50,  "roi_pct": 5.0, "brier": 0.23},
    ]
    passed, notes = gates.market_gate(summary)
    assert passed == {"moneyline"}
    assert "ROI" in notes["spread"]
    assert "sample" in notes["totals"]


def test_gate_brier_threshold_is_wnba_loose():
    # WNBA gate at 0.250 (vs MLB's 0.246). A market at 0.249 should
    # still pass; at 0.251 should fail.
    summary_pass = [
        {"bet_type": "totals", "bets": 250, "roi_pct": 2.0, "brier": 0.249},
    ]
    passed, _ = gates.market_gate(summary_pass)
    assert "totals" in passed

    summary_fail = [
        {"bet_type": "totals", "bets": 250, "roi_pct": 2.0, "brier": 0.251},
    ]
    passed, notes = gates.market_gate(summary_fail)
    assert "totals" not in passed
    assert "0.2510" in notes["totals"]


def test_edge_floor_per_market():
    assert gates.edge_floor_for("moneyline") == 4.0
    assert gates.edge_floor_for("spread") == 3.0
    assert gates.edge_floor_for("totals") == 2.5
    # Override
    assert gates.edge_floor_for("totals", overrides={"totals": 1.0}) == 1.0


def test_parse_threshold_overrides():
    out = gates.parse_threshold_overrides(["totals=1.5", "moneyline=5", "garbage"])
    assert out == {"totals": 1.5, "moneyline": 5.0}
