"""Tests for the props historical-backtest pipeline.

Covers:
  * Boxscore record extraction (using a synthetic boxscore payload that
    mirrors the MLB Stats API shape we observed in the existing scrapers).
  * PlayerHistory's chronological "no look-ahead" rate computation.
  * PropsBacktestEngine end-to-end on a tiny synthetic dataset.
  * select_summary_for_gate + market_gate for props.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_equation.engines.props_prizepicks.gates import (
    MAX_GATE_BRIER, MIN_GATE_BETS, MIN_GATE_ROI,
    edge_floor_for, market_gate, select_summary_for_gate,
)
from edge_equation.exporters.mlb.props_backtest import (
    BATTER_LINES, FLAT_DECIMAL_ODDS, PITCHER_LINES, PlayerHistory,
    PropsBacktestEngine, _BatterAgg, _PitcherAgg,
    _bayesian_blend, _prob_over_poisson, prob_floor_for_props,
)


# Import the module-style helper too so we can hit boxscore parsing
# directly without touching the network.
from scripts.backfill_player_games import _extract_player_records  # type: ignore


# ---------------------------------------------------------------------
# Boxscore extraction
# ---------------------------------------------------------------------

def _synth_boxscore() -> dict:
    """A two-side boxscore mirroring the MLB Stats API shape."""
    return {
        "teams": {
            "home": {
                "team": {"id": 121, "abbreviation": "HOM"},
                "battingOrder": [10, 11, 12, 13, 14, 15, 16, 17, 18],
                "pitchers": [50, 51],
                "players": {
                    "ID10": {
                        "person": {"id": 10, "fullName": "Home Bat 1"},
                        "position": {"abbreviation": "RF"},
                        "stats": {
                            "batting": {
                                "plateAppearances": 4, "atBats": 4,
                                "hits": 2, "doubles": 1, "triples": 0,
                                "homeRuns": 1, "rbi": 3, "totalBases": 6,
                                "baseOnBalls": 0, "strikeOuts": 1,
                            },
                            "pitching": {},
                        },
                    },
                    "ID50": {
                        "person": {"id": 50, "fullName": "Home Pitcher"},
                        "position": {"abbreviation": "P"},
                        "stats": {
                            "batting": {},
                            "pitching": {
                                "battersFaced": 25,
                                "inningsPitched": "6.1",
                                "strikeOuts": 8, "earnedRuns": 2,
                                "hits": 4, "homeRuns": 1, "baseOnBalls": 1,
                            },
                        },
                    },
                },
            },
            "away": {
                "team": {"id": 122, "abbreviation": "AWY"},
                "battingOrder": [20, 21, 22, 23, 24, 25, 26, 27, 28],
                "pitchers": [60],
                "players": {
                    "ID20": {
                        "person": {"id": 20, "fullName": "Away Bat 1"},
                        "position": {"abbreviation": "1B"},
                        "stats": {
                            "batting": {
                                "plateAppearances": 5, "atBats": 5,
                                "hits": 0, "homeRuns": 0, "rbi": 0,
                                "totalBases": 0, "baseOnBalls": 0,
                                "strikeOuts": 3,
                            },
                            "pitching": {},
                        },
                    },
                },
            },
        },
    }


def test_extract_player_records_emits_batter_and_pitcher():
    rows = _extract_player_records(
        game_pk=999, date="2024-04-01",
        home_team="HOM", away_team="AWY",
        boxscore=_synth_boxscore(),
    )
    # 2 batters + 1 pitcher = 3 rows
    roles = sorted([r["role"] for r in rows])
    assert roles == ["batter", "batter", "pitcher"]
    pit = next(r for r in rows if r["role"] == "pitcher")
    assert pit["started"] is True
    assert pit["stats"]["strikeOuts"] == 8
    assert pit["stats"]["inningsPitched"] == pytest.approx(6 + 1 / 3.0)
    bat = next(r for r in rows if r["player_name"] == "Home Bat 1")
    assert bat["stats"]["homeRuns"] == 1
    assert bat["stats"]["totalBases"] == 6


def test_extract_skips_zero_pa_zero_bf():
    box = {
        "teams": {
            "home": {
                "team": {"id": 1, "abbreviation": "X"},
                "players": {
                    "ID9": {
                        "person": {"id": 9, "fullName": "Bench"},
                        "stats": {"batting": {"plateAppearances": 0},
                                  "pitching": {"battersFaced": 0}},
                    }
                },
            },
            "away": {"team": {"id": 2, "abbreviation": "Y"}, "players": {}},
        },
    }
    assert _extract_player_records(1, "d", "X", "Y", box) == []


# ---------------------------------------------------------------------
# PlayerHistory walk
# ---------------------------------------------------------------------

def test_player_history_no_look_ahead():
    hist = PlayerHistory()
    # Game 1: 4 PAs, 1 HR
    hist.update_with_row({
        "player_id": 7, "role": "batter",
        "stats": {"plateAppearances": 4, "homeRuns": 1, "hits": 2,
                  "totalBases": 5, "rbi": 2},
    })
    # After game 1 the player's HR rate = 1/4
    rate, n = hist.batter_rate(7, "HR")
    assert n == 4
    assert rate == pytest.approx(0.25)

    # Game 2: 5 PAs, 0 HR
    hist.update_with_row({
        "player_id": 7, "role": "batter",
        "stats": {"plateAppearances": 5, "homeRuns": 0, "hits": 1,
                  "totalBases": 1, "rbi": 0},
    })
    # Cumulative now: 1 HR / 9 PA
    rate, n = hist.batter_rate(7, "HR")
    assert n == 9
    assert rate == pytest.approx(1 / 9)


# ---------------------------------------------------------------------
# Math sanity
# ---------------------------------------------------------------------

def test_bayesian_blend_falls_back_to_prior_at_zero_n():
    assert _bayesian_blend(0.5, 0, 0.1, 50.0) == pytest.approx(0.1)


def test_bayesian_blend_pulls_toward_prior():
    no_shrink_obs = 0.30
    blended = _bayesian_blend(no_shrink_obs, 10, 0.10, 50.0)
    assert 0.10 < blended < no_shrink_obs


def test_poisson_prob_over_monotonic_in_lambda():
    p_low = _prob_over_poisson(0.5, 0.2)
    p_high = _prob_over_poisson(0.5, 0.8)
    assert p_low < p_high


def test_prob_floor_at_flat_price():
    # 5% edge floor for HR at decimal 1.909
    assert prob_floor_for_props("HR") == pytest.approx(
        (1 + 5.0 / 100) / 1.909, rel=1e-4,
    )


# ---------------------------------------------------------------------
# Engine end-to-end on a synthetic micro-dataset
# ---------------------------------------------------------------------

def _synth_player_games(n_days: int, hr_rate_per_pa: float = 0.04) -> list:
    """Build n_days rows for one batter who hits HRs at a fixed rate.

    The expected_batter_pa is 4.1, so per-game HR expectation is
    4.1 * hr_rate_per_pa. We sprinkle 4-PA games and let the binomial
    pattern drive actuals.
    """
    rows = []
    for d in range(n_days):
        # Deterministic pattern: every 25th game has a HR; otherwise 0.
        hr = 1 if (d % 25 == 0 and d > 0) else 0
        rows.append({
            "game_pk": 1000 + d, "date": f"2024-04-{d % 28 + 1:02d}",
            "team": "HOM", "opponent": "AWY", "is_home": True,
            "role": "batter", "player_id": 7, "player_name": "Test Bat",
            "started": True,
            "stats": {
                "plateAppearances": 4, "atBats": 4,
                "hits": 1 if hr else 0, "homeRuns": hr,
                "totalBases": 4 if hr else 0,
                "rbi": 1 if hr else 0,
            },
        })
    return rows


def test_engine_emits_summary_shape_matching_mlb_backtest():
    rows = _synth_player_games(60)
    engine = PropsBacktestEngine(rows=rows, min_history_pa=20)
    res = engine.run()
    # The shape is a strict superset of what `select_summary_for_gate`
    # consumes -- this is what gives us a "drop-in" with the MLB pattern.
    assert "summary_by_bet_type" in res
    assert "summary_by_bet_type_play_only" in res
    assert "overall" in res
    assert "overall_play_only" in res
    # Some bets should have been graded once history > 20 PA.
    assert res["n_bets"] > 0


def test_engine_play_only_filter_subsets_or_equals():
    rows = _synth_player_games(50)
    engine = PropsBacktestEngine(rows=rows, min_history_pa=20)
    res = engine.run()
    by_all = {r["bet_type"]: r["bets"] for r in res["summary_by_bet_type"]}
    by_play = {
        r["bet_type"]: r["bets"]
        for r in res["summary_by_bet_type_play_only"]
    }
    for bt, n in by_play.items():
        assert n <= by_all.get(bt, 0)


def test_calibration_can_be_disabled():
    rows = _synth_player_games(40)
    e_on = PropsBacktestEngine(rows=rows, min_history_pa=20,
                               apply_calibration=True)
    e_off = PropsBacktestEngine(rows=rows, min_history_pa=20,
                                apply_calibration=False)
    assert e_on.run()["apply_calibration"] is True
    assert e_off.run()["apply_calibration"] is False


# ---------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------

def test_props_market_gate_passes_clean_row():
    summary = [
        {"bet_type": "HR",   "bets": 500, "roi_pct": 5.0, "brier": 0.24},
        {"bet_type": "Hits", "bets": 50,  "roi_pct": 5.0, "brier": 0.24},
        {"bet_type": "K",    "bets": 500, "roi_pct": -1.0, "brier": 0.24},
    ]
    passed, notes = market_gate(summary)
    assert passed == {"HR"}
    assert "Hits" in notes and "K" in notes


def test_props_select_summary_prefers_play_only():
    payload = {
        "summary_by_bet_type": [{"bet_type": "x", "bets": 100}],
        "summary_by_bet_type_play_only": [{"bet_type": "y", "bets": 200}],
    }
    summary, label = select_summary_for_gate(payload)
    assert label == "play_only"
    assert summary[0]["bet_type"] == "y"


def test_props_edge_floor_overrides():
    assert edge_floor_for("HR") == 5.0
    assert edge_floor_for("HR", overrides={"HR": 8.0}) == 8.0
    assert edge_floor_for("unknown_market") == 4.0  # default
