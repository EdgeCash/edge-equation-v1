"""Tests for the shared NBA/NHL game-results backtest core."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_equation.exporters._game_results_backtest import (
    GameResultsBacktestEngine, NBA_CONFIG, NHL_CONFIG, SportConfig,
    TeamHistory, _gaussian_cdf, _logistic, _settle, load_games_jsonl,
)


# ---------------------------------------------------------------------
# Math primitives
# ---------------------------------------------------------------------

def test_logistic_centered_at_zero():
    assert _logistic(0.0, 10.0) == pytest.approx(0.5)


def test_logistic_monotonic():
    assert _logistic(-5.0, 10.0) < _logistic(0.0, 10.0) < _logistic(5.0, 10.0)


def test_logistic_clamps_extremes():
    assert 0.0 <= _logistic(1e9, 1.0) <= 1.0
    assert 0.0 <= _logistic(-1e9, 1.0) <= 1.0


def test_gaussian_cdf_monotonic():
    assert _gaussian_cdf(190, 200, 10) < _gaussian_cdf(210, 200, 10)


def test_gaussian_cdf_at_mean_is_half():
    assert _gaussian_cdf(200, 200, 10) == pytest.approx(0.5, abs=1e-6)


def test_settle_handles_push_win_loss():
    assert _settle(1.909, won=True, push=False) == pytest.approx(0.909, abs=1e-3)
    assert _settle(1.909, won=False, push=False) == -1.0
    assert _settle(1.909, won=False, push=True) == 0.0


# ---------------------------------------------------------------------
# TeamHistory rolling stats
# ---------------------------------------------------------------------

def test_team_history_empty_team_has_zero_stats():
    h = TeamHistory()
    a = h.get("AAA")
    assert a.games == 0 and a.ppg == 0.0


def test_team_history_updates_both_sides():
    h = TeamHistory()
    h.update("AAA", "BBB", 100, 95)
    a, b = h.get("AAA"), h.get("BBB")
    assert a.games == 1 and a.points_for == 100 and a.points_against == 95
    assert b.games == 1 and b.points_for == 95 and b.points_against == 100
    assert a.wins == 1
    assert b.wins == 0


def test_team_history_aggregates_chronologically():
    h = TeamHistory()
    h.update("AAA", "BBB", 110, 100)
    h.update("AAA", "CCC", 90, 95)
    a = h.get("AAA")
    assert a.games == 2
    assert a.ppg == 100.0  # (110 + 90) / 2
    assert a.papg == 97.5  # (100 + 95) / 2


# ---------------------------------------------------------------------
# Engine end-to-end on synthetic data
# ---------------------------------------------------------------------

def _synth_nba(n_games: int = 80) -> list[dict]:
    """Round-robin schedule across 4 teams with stable scoring tendencies.
    AAA + BBB are high-scoring, CCC + DDD low-scoring -- gives the
    backtest something to project against."""
    rows = []
    teams = ["AAA", "BBB", "CCC", "DDD"]
    base = {"AAA": 115, "BBB": 113, "CCC": 105, "DDD": 103}
    for i in range(n_games):
        h = teams[i % 4]
        a = teams[(i + 1) % 4]
        # Slight noise around the team's base scoring.
        h_score = base[h] + (i % 7) - 3
        a_score = base[a] + ((i * 3) % 7) - 3
        rows.append({
            "date": f"2024-11-{i // 8 + 1:02d}",
            "game_id": f"g{i:04d}",
            "completed": True,
            "away_team": a, "home_team": h,
            "away_score": a_score, "home_score": h_score,
            "total_points": h_score + a_score,
            "ml_winner": h if h_score > a_score else a,
            "margin": abs(h_score - a_score),
        })
    return rows


def test_engine_emits_expected_shape():
    rows = _synth_nba(60)
    res = GameResultsBacktestEngine(rows=rows, cfg=NBA_CONFIG).run()
    for key in ("summary_by_bet_type", "summary_by_bet_type_play_only",
                "overall", "overall_play_only", "n_games_in_window"):
        assert key in res
    bet_types = {r["bet_type"] for r in res["summary_by_bet_type"]}
    assert {"moneyline", "spread", "totals"} <= bet_types


def test_engine_play_only_is_subset_of_all():
    rows = _synth_nba(80)
    res = GameResultsBacktestEngine(rows=rows, cfg=NBA_CONFIG).run()
    by_all = {r["bet_type"]: r["bets"] for r in res["summary_by_bet_type"]}
    by_play = {r["bet_type"]: r["bets"]
               for r in res["summary_by_bet_type_play_only"]}
    for bt, n in by_play.items():
        assert n <= by_all.get(bt, 0)


def test_engine_skips_until_min_history():
    """First few games per team must NOT be graded -- there's no
    rolling rate yet to project against."""
    rows = _synth_nba(20)
    res = GameResultsBacktestEngine(rows=rows, cfg=NBA_CONFIG).run()
    # Min history default is 5 per team. With round-robin across 4 teams,
    # each team needs ~5 prior games -> we lose ~5 rounds = 20 games
    # before grading begins. So with 20 games total, expect very few bets.
    by_all = {r["bet_type"]: r["bets"] for r in res["summary_by_bet_type"]}
    assert by_all.get("moneyline", 0) < 20  # most rows skipped


def test_nhl_config_uses_total_goals_field():
    """NHL config must read goals (lower SD) not points."""
    assert NHL_CONFIG.points_field == "total_goals"
    assert NHL_CONFIG.spread_line == 1.5
    assert NHL_CONFIG.margin_sd < NBA_CONFIG.margin_sd


def test_load_games_jsonl_handles_sentinels(tmp_path: Path):
    p = tmp_path / "2024" / "games.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        '{"_no_games": true, "_date_ymd": "20240101"}\n'
        '{"_error": "x"}\n'
        '{"completed": false, "away_team": "X", "home_team": "Y"}\n'
        '{"completed": true, "date": "2024-04-01", "game_id": "g1", '
        '"away_team": "AAA", "home_team": "BBB", '
        '"away_score": 100, "home_score": 110}\n'
    )
    rows = load_games_jsonl([2024], tmp_path)
    assert len(rows) == 1
    assert rows[0]["game_id"] == "g1"


def test_load_games_jsonl_chronologically_sorted(tmp_path: Path):
    p = tmp_path / "2024" / "games.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        '{"completed": true, "date": "2024-04-10", "game_id": "z", '
        '"away_team": "A", "home_team": "B", "away_score": 1, "home_score": 2}\n'
        '{"completed": true, "date": "2024-04-01", "game_id": "a", '
        '"away_team": "C", "home_team": "D", "away_score": 1, "home_score": 2}\n'
    )
    rows = load_games_jsonl([2024], tmp_path)
    assert [r["date"] for r in rows] == ["2024-04-01", "2024-04-10"]


def test_load_games_jsonl_handles_missing_seasons(tmp_path: Path):
    rows = load_games_jsonl([2024, 9999], tmp_path)
    assert rows == []
