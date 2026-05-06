"""Smoke + parsing tests for the overnight backfill harvesters.

We can't hit ESPN / MLB Stats API from CI, so these tests cover the
pieces that don't need the network:

  * Synthetic ESPN scoreboard payloads -> NBA / NHL row extraction.
  * Synthetic ESPN boxscore summary payload -> WNBA player rows.
  * Resumable JSONL utilities (scan_completed_ids).
  * harvest_overnight CLI argparse + league dispatch.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add the scripts dir to sys.path so the test can import them as modules.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _harvest_common import scan_completed_ids  # type: ignore  # noqa: E402
import backfill_nba_games as nba  # type: ignore  # noqa: E402
import backfill_nhl_games as nhl  # type: ignore  # noqa: E402
import backfill_wnba_player_games as wnba_pl  # type: ignore  # noqa: E402
import harvest_overnight as orchestrator  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------
# scan_completed_ids
# ---------------------------------------------------------------------

def test_scan_completed_ids_handles_missing(tmp_path: Path):
    assert scan_completed_ids(tmp_path / "nope.jsonl") == set()


def test_scan_completed_ids_skips_blank_and_malformed(tmp_path: Path):
    p = tmp_path / "log.jsonl"
    p.write_text(
        '{"game_id": "1"}\n'
        '\n'                             # blank
        '{not-json}\n'                   # malformed
        '{"game_id": "2"}\n'
        '{"other_field": "ignored"}\n'   # missing id field
    )
    assert scan_completed_ids(p, id_field="game_id") == {"1", "2"}


# ---------------------------------------------------------------------
# NBA / NHL scoreboard parsing
# ---------------------------------------------------------------------

_SENTINEL = object()


def _scoreboard_payload(home_score: int, away_score: int,
                        completed: bool = True,
                        away_lines=_SENTINEL, home_lines=_SENTINEL) -> dict:
    if away_lines is _SENTINEL:
        away_lines = [{"value": 25}, {"value": 30}]
    if home_lines is _SENTINEL:
        home_lines = [{"value": 28}, {"value": 27}]
    return {
        "events": [
            {
                "id": "evt1",
                "season": {"type": 2},
                "competitions": [
                    {
                        "id": "comp1",
                        "date": "2024-11-01T00:00Z",
                        "venue": {"fullName": "Test Arena"},
                        "status": {"type": {
                            "name": "STATUS_FINAL",
                            "completed": completed,
                        }},
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": str(home_score),
                                "team": {"abbreviation": "HOM"},
                                "linescores": home_lines,
                            },
                            {
                                "homeAway": "away",
                                "score": str(away_score),
                                "team": {"abbreviation": "AWY"},
                                "linescores": away_lines,
                            },
                        ],
                    },
                ],
            },
        ],
    }


def test_nba_parse_scoreboard_picks_home_winner_when_home_higher():
    rows = nba.parse_scoreboard(_scoreboard_payload(110, 95))
    assert len(rows) == 1
    r = rows[0]
    assert r["home_team"] == "HOM"
    assert r["away_team"] == "AWY"
    assert r["ml_winner"] == "HOM"
    assert r["margin"] == 15
    assert r["total_points"] == 205


def test_nba_parse_scoreboard_skips_in_progress_games():
    assert nba.parse_scoreboard(
        _scoreboard_payload(80, 80, completed=False),
    ) == []


def test_nba_parse_scoreboard_handles_missing_linescores():
    payload = _scoreboard_payload(110, 95, away_lines=[], home_lines=[])
    r = nba.parse_scoreboard(payload)[0]
    assert r["home_q"] == [] and r["away_q"] == []
    assert r["home_1h"] is None and r["away_1h"] is None


def test_nhl_parse_scoreboard_marks_overtime():
    payload = _scoreboard_payload(
        4, 3,
        # 4 periods means OT happened
        away_lines=[{"value": 1}, {"value": 1}, {"value": 1}, {"value": 0}],
        home_lines=[{"value": 1}, {"value": 1}, {"value": 1}, {"value": 1}],
    )
    r = nhl.parse_scoreboard(payload)[0]
    assert r["had_ot"] is True
    assert r["regulation_total"] == 6
    assert r["regulation_winner"] == "TIE"
    assert r["ml_winner"] == "HOM"


def test_nhl_parse_scoreboard_regulation_only_no_ot():
    payload = _scoreboard_payload(
        4, 3,
        away_lines=[{"value": 1}, {"value": 1}, {"value": 1}],
        home_lines=[{"value": 2}, {"value": 1}, {"value": 1}],
    )
    r = nhl.parse_scoreboard(payload)[0]
    assert r["had_ot"] is False
    assert r["regulation_winner"] == "HOM"


# ---------------------------------------------------------------------
# WNBA boxscore summary parsing
# ---------------------------------------------------------------------

def _summary_payload() -> dict:
    return {
        "boxscore": {
            "players": [
                {
                    "team": {"id": "1", "abbreviation": "HOM"},
                    "statistics": [
                        {
                            "labels": [
                                "MIN", "FG", "3PT", "FT", "OREB", "DREB",
                                "REB", "AST", "STL", "BLK", "TO", "PF", "PTS",
                            ],
                            "athletes": [
                                {
                                    "athlete": {
                                        "id": "1001",
                                        "displayName": "Star Player",
                                        "position": {"abbreviation": "G"},
                                    },
                                    "starter": True,
                                    "stats": [
                                        "32:15", "9-18", "3-7", "5-6", "1", "4",
                                        "5", "7", "2", "0", "3", "2", "26",
                                    ],
                                },
                                {
                                    "athlete": {
                                        "id": "1002",
                                        "displayName": "Bench Player",
                                    },
                                    "starter": False,
                                    "didNotPlay": False,
                                    "stats": [
                                        "12:00", "2-5", "1-3", "0-0", "0", "2",
                                        "2", "1", "0", "0", "1", "1", "5",
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    }


def test_wnba_parse_summary_extracts_one_row_per_player():
    rows = wnba_pl.parse_summary(_summary_payload(), {
        "game_id": "g1", "date": "2024-08-15",
        "home_team": "HOM", "away_team": "AWY", "home_id": "1",
    })
    assert len(rows) == 2
    star = next(r for r in rows if r["player_name"] == "Star Player")
    assert star["points"] == 26
    assert star["rebounds"] == 5
    assert star["assists"] == 7
    assert star["fg_made"] == 9 and star["fg_att"] == 18
    assert star["three_made"] == 3 and star["three_att"] == 7
    assert star["pra"] == 26 + 5 + 7


def test_wnba_parse_summary_handles_minutes_as_mmss():
    rows = wnba_pl.parse_summary(_summary_payload(), {
        "game_id": "g1", "date": "2024-08-15",
        "home_team": "HOM", "away_team": "AWY", "home_id": "1",
    })
    star = next(r for r in rows if r["player_name"] == "Star Player")
    # 32:15 -> 32 + 15/60 ~= 32.25
    assert star["minutes"] == pytest.approx(32 + 15 / 60.0)


def test_wnba_parse_summary_empty_payload_is_safe():
    assert wnba_pl.parse_summary({}, {"game_id": "g1"}) == []


# ---------------------------------------------------------------------
# Orchestrator dispatch
# ---------------------------------------------------------------------

def test_orchestrator_known_leagues_have_scripts():
    for league in orchestrator.LEAGUE_SCRIPTS:
        path = orchestrator.SCRIPTS_DIR / orchestrator.LEAGUE_SCRIPTS[league]
        assert path.exists(), f"{league} script missing"


def test_orchestrator_default_seasons_match_league_keys():
    assert set(orchestrator.DEFAULT_SEASONS) == set(orchestrator.LEAGUE_SCRIPTS)


def test_orchestrator_run_league_invokes_subprocess(monkeypatch):
    captured = {}
    def fake_run(cmd, *args, **kwargs):
        class _Result:
            returncode = 0
        captured["cmd"] = cmd
        return _Result()
    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    rc = orchestrator.run_league("nba", [2024, 2025], rps=0.5, limit=10)
    assert rc == 0
    assert captured["cmd"][0] == orchestrator.PYTHON
    assert "--seasons" in captured["cmd"]
    assert "2024" in captured["cmd"] and "2025" in captured["cmd"]
    assert "--rps" in captured["cmd"] and "0.5" in captured["cmd"]
    assert "--limit" in captured["cmd"] and "10" in captured["cmd"]
