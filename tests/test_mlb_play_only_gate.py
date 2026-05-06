"""Tests for the MLB play-only gate + calibration shrink.

Covers the May-2026 tightening where the BRAND_GUIDE gate was
refactored to consume `summary_by_bet_type_play_only` (the slice
matching the production filter) and a per-market temperature shrink
was applied uniformly across backtest + projections.

Each test is self-contained and uses a tiny synthetic backfill so
it runs in milliseconds.
"""
from __future__ import annotations

import pytest

from edge_equation.exporters.mlb.backtest import (
    BacktestEngine, DEFAULT_TEMPERATURE, calibrate_prob, _shrink_prob,
)
from edge_equation.exporters.mlb.gates import (
    edge_floor_for, market_gate, prob_floor_for, select_summary_for_gate,
)


# ---------------------------------------------------------------------
# Calibration shrink
# ---------------------------------------------------------------------

def test_shrink_prob_pulls_toward_50():
    assert _shrink_prob(0.6, 0.5) == pytest.approx(0.55)
    assert _shrink_prob(0.4, 0.5) == pytest.approx(0.45)
    # tau=1.0 is identity
    assert _shrink_prob(0.7, 1.0) == pytest.approx(0.7)


def test_calibrate_prob_uses_default_per_market_tau():
    # moneyline is the most aggressive shrinker.
    cal_ml = calibrate_prob(0.6, "moneyline")
    cal_rl = calibrate_prob(0.6, "run_line")
    assert cal_ml < cal_rl  # ML pulled harder toward 0.5 than RL


def test_calibrate_prob_unknown_market_is_identity():
    assert calibrate_prob(0.6, "exotic_market") == pytest.approx(0.6)


# ---------------------------------------------------------------------
# prob_floor_for
# ---------------------------------------------------------------------

def test_prob_floor_matches_edge_floor_at_flat_price():
    # At -110 (decimal 1.909) a 4% edge floor implies prob >= ~0.5448
    floor = prob_floor_for("moneyline")
    assert floor == pytest.approx((1 + 4.0 / 100.0) / 1.909, rel=1e-4)


def test_prob_floor_respects_overrides():
    overrides = {"totals": 5.0}
    base = prob_floor_for("totals")
    bumped = prob_floor_for("totals", overrides=overrides)
    assert bumped > base


# ---------------------------------------------------------------------
# select_summary_for_gate
# ---------------------------------------------------------------------

def test_select_summary_prefers_play_only():
    payload = {
        "summary_by_bet_type": [{"bet_type": "x", "bets": 100, "roi_pct": 0}],
        "summary_by_bet_type_play_only": [
            {"bet_type": "y", "bets": 200, "roi_pct": 5},
        ],
    }
    summary, label = select_summary_for_gate(payload)
    assert label == "play_only"
    assert summary[0]["bet_type"] == "y"


def test_select_summary_falls_back_to_all_bets():
    payload = {"summary_by_bet_type": [{"bet_type": "x", "bets": 200}]}
    summary, label = select_summary_for_gate(payload)
    assert label == "all"
    assert summary[0]["bet_type"] == "x"


def test_select_summary_handles_none():
    summary, label = select_summary_for_gate(None)
    assert summary is None
    assert label == "none"


# ---------------------------------------------------------------------
# Backtest play-only summary
# ---------------------------------------------------------------------

def _synth_game(date: str, away: str, home: str,
                away_score: int, home_score: int) -> dict:
    """Minimal game record the BacktestEngine grader consumes."""
    f5_away = away_score // 2
    f5_home = home_score // 2
    if f5_away > f5_home:
        f5_winner = away
    elif f5_home > f5_away:
        f5_winner = home
    else:
        f5_winner = "PUSH"
    ml_winner = home if home_score > away_score else away
    f1_away = 1 if away_score >= 4 else 0
    f1_home = 1 if home_score >= 4 else 0
    return {
        "date": date,
        "away_team": away, "home_team": home,
        "away_score": away_score, "home_score": home_score,
        "total": away_score + home_score,
        "f5_away": f5_away, "f5_home": f5_home,
        "f5_winner": f5_winner,
        "f1_away": f1_away, "f1_home": f1_home,
        "ml_winner": ml_winner,
        "nrfi": not (f1_away or f1_home),
        "game_pk": hash(f"{date}{away}{home}") % 10_000_000,
    }


@pytest.fixture
def synth_backfill():
    """A minimal but plausible 60-game backfill across 4 teams."""
    teams = ["AAA", "BBB", "CCC", "DDD"]
    games = []
    rng_idx = 0
    for d in range(60):
        date = f"2025-04-{d % 30 + 1:02d}"
        i = (d * 7) % 4
        j = (d * 11 + 1) % 4
        if i == j:
            j = (j + 1) % 4
        away = teams[i]
        home = teams[j]
        a = (rng_idx * 13) % 9
        h = (rng_idx * 17 + 3) % 9
        rng_idx += 1
        games.append(_synth_game(date, away, home, a, h))
    return games


def test_run_emits_both_summaries(synth_backfill):
    engine = BacktestEngine(synth_backfill, min_history=5)
    result = engine.run()
    assert "summary_by_bet_type" in result
    assert "summary_by_bet_type_play_only" in result
    assert "calibration_temperature" in result
    # Play-only is a subset (or equal) of the all-bets summary, never larger.
    by_all = {r["bet_type"]: r["bets"] for r in result["summary_by_bet_type"]}
    by_play = {r["bet_type"]: r["bets"]
               for r in result["summary_by_bet_type_play_only"]}
    for bt, n_play in by_play.items():
        assert n_play <= by_all.get(bt, 0)


def test_calibration_can_be_disabled(synth_backfill):
    engine_off = BacktestEngine(
        synth_backfill, min_history=5, apply_calibration=False,
    )
    engine_on = BacktestEngine(synth_backfill, min_history=5)
    res_off = engine_off.run()
    res_on = engine_on.run()
    assert res_off["apply_calibration"] is False
    assert res_on["apply_calibration"] is True


# ---------------------------------------------------------------------
# market_gate end-to-end with the play-only summary
# ---------------------------------------------------------------------

def test_market_gate_passes_play_only_clean_row():
    summary = [
        {"bet_type": "totals", "bets": 500, "roi_pct": 5.0, "brier": 0.24},
        {"bet_type": "moneyline", "bets": 500, "roi_pct": -2.0, "brier": 0.255},
    ]
    passed, notes = market_gate(summary)
    assert passed == {"totals"}
    assert "moneyline" in notes
