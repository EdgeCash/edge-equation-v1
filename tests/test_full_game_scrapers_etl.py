"""Tests for the full-game ETL — linescore parsers + backfill orchestrator."""

from __future__ import annotations

from typing import Any

import pytest

from edge_equation.engines.full_game.data.scrapers_etl import (
    backfill_fullgame_actuals,
    first_five_innings_runs,
    full_game_runs,
)


# ---------------------------------------------------------------------------
# Linescore parsers
# ---------------------------------------------------------------------------


def _ls(*innings_runs):
    """Build a linescore dict from ``[(num, home, away), ...]``."""
    return {
        "innings": [
            {"num": num, "home": {"runs": h}, "away": {"runs": a}}
            for num, h, a in innings_runs
        ]
    }


def test_full_game_runs_sums_all_innings():
    ls = _ls((1, 0, 1), (2, 2, 0), (3, 1, 1))
    assert full_game_runs(ls) == (3, 2)


def test_full_game_runs_handles_empty_linescore():
    assert full_game_runs({"innings": []}) == (None, None)
    assert full_game_runs({}) == (None, None)


def test_full_game_runs_treats_missing_inning_runs_as_zero():
    ls = {"innings": [{"num": 1, "home": {}, "away": {"runs": 2}}]}
    assert full_game_runs(ls) == (0, 2)


def test_first_five_returns_runs_only_for_innings_1_through_5():
    ls = _ls((1, 1, 0), (2, 0, 2), (3, 1, 0), (4, 0, 0), (5, 1, 0),
              (6, 5, 5), (7, 0, 0))   # innings 6+ should be ignored
    assert first_five_innings_runs(ls) == (3, 2)


def test_first_five_returns_none_when_fewer_than_five_innings():
    """Game in progress or short game — no F5 result."""
    ls = _ls((1, 1, 0), (2, 0, 2), (3, 1, 0))
    assert first_five_innings_runs(ls) == (None, None)


def test_first_five_handles_extra_innings_correctly():
    ls = _ls((1, 0, 0), (2, 1, 1), (3, 0, 0), (4, 0, 0), (5, 0, 0),
              (6, 0, 0), (7, 0, 0), (8, 0, 0), (9, 0, 0), (10, 1, 0))
    assert first_five_innings_runs(ls) == (1, 1)


# ---------------------------------------------------------------------------
# Backfill orchestrator (with fake client)
# ---------------------------------------------------------------------------


class _FakeStub:
    def __init__(self, game_pk: int, home: str, away: str):
        self.game_pk = game_pk
        self.home_team = home
        self.away_team = away


class _FakeClient:
    def __init__(self, schedule_by_date: dict, linescore_by_pk: dict):
        self._schedule = schedule_by_date
        self._linescores = linescore_by_pk
        self.closed = False

    def schedule(self, date_iso: str):
        return list(self._schedule.get(date_iso, []))

    def linescore(self, game_pk: int):
        return self._linescores.get(int(game_pk), {})

    def close(self):
        self.closed = True


class _FakeStore:
    def __init__(self):
        self.upserts: list[tuple[str, list[dict]]] = []

    def upsert(self, table: str, rows):
        rows = list(rows)
        self.upserts.append((table, rows))
        return len(rows)


def test_backfill_persists_completed_games_only():
    """Completed games go to fullgame_actuals; in-progress / postponed
    games (empty linescore) are skipped silently."""
    schedule = {
        "2026-04-01": [
            _FakeStub(11, "NYY", "BOS"),
            _FakeStub(12, "LAD", "SD"),
            _FakeStub(13, "ATL", "PHI"),  # postponed → empty linescore
        ],
    }
    linescores = {
        11: {"innings": [{"num": i, "home": {"runs": 0}, "away": {"runs": 0}}
                            for i in range(1, 10)]},
        12: {"innings": [{"num": i, "home": {"runs": 1}, "away": {"runs": 0}}
                            for i in range(1, 10)]},
        13: {"innings": []},  # postponed
    }
    client = _FakeClient(schedule, linescores)
    store = _FakeStore()
    n = backfill_fullgame_actuals(
        "2026-04-01", "2026-04-01", store, client=client,
    )
    assert n == 2
    table, rows = store.upserts[0]
    assert table == "fullgame_actuals"
    pks = sorted(r["game_pk"] for r in rows)
    assert pks == [11, 12]
    # Persisted teams come from the schedule stub.
    home_teams = {r["game_pk"]: r["home_team"] for r in rows}
    assert home_teams[11] == "NYY"
    assert home_teams[12] == "LAD"


def test_backfill_walks_date_range_inclusive():
    """A 3-day window calls schedule three times."""
    schedule = {
        "2026-04-01": [_FakeStub(1, "NYY", "BOS")],
        "2026-04-02": [_FakeStub(2, "NYY", "BOS")],
        "2026-04-03": [_FakeStub(3, "NYY", "BOS")],
    }
    nine_zeros = {"innings": [{"num": i, "home": {"runs": 0},
                                  "away": {"runs": 0}} for i in range(1, 10)]}
    linescores = {1: nine_zeros, 2: nine_zeros, 3: nine_zeros}
    client = _FakeClient(schedule, linescores)
    store = _FakeStore()
    n = backfill_fullgame_actuals(
        "2026-04-01", "2026-04-03", store, client=client,
    )
    assert n == 3


def test_backfill_swallows_per_game_linescore_errors():
    """One bad linescore fetch shouldn't kill the whole window."""
    schedule = {
        "2026-04-01": [
            _FakeStub(1, "NYY", "BOS"),
            _FakeStub(2, "LAD", "SD"),
        ],
    }

    class _FlakyClient(_FakeClient):
        def linescore(self, game_pk: int):
            if int(game_pk) == 1:
                raise RuntimeError("HTTP 503")
            return super().linescore(game_pk)

    linescores = {
        2: {"innings": [{"num": i, "home": {"runs": 0}, "away": {"runs": 0}}
                          for i in range(1, 10)]},
    }
    client = _FlakyClient(schedule, linescores)
    store = _FakeStore()
    n = backfill_fullgame_actuals(
        "2026-04-01", "2026-04-01", store, client=client,
    )
    assert n == 1
    assert store.upserts[0][1][0]["game_pk"] == 2


def test_backfill_persists_f5_runs_when_complete():
    schedule = {"2026-04-01": [_FakeStub(1, "NYY", "BOS")]}
    linescores = {
        1: {
            "innings": [
                {"num": 1, "home": {"runs": 1}, "away": {"runs": 0}},
                {"num": 2, "home": {"runs": 0}, "away": {"runs": 2}},
                {"num": 3, "home": {"runs": 1}, "away": {"runs": 0}},
                {"num": 4, "home": {"runs": 0}, "away": {"runs": 0}},
                {"num": 5, "home": {"runs": 0}, "away": {"runs": 1}},
                {"num": 6, "home": {"runs": 1}, "away": {"runs": 0}},
                {"num": 7, "home": {"runs": 0}, "away": {"runs": 0}},
                {"num": 8, "home": {"runs": 0}, "away": {"runs": 0}},
                {"num": 9, "home": {"runs": 0}, "away": {"runs": 0}},
            ],
        },
    }
    client = _FakeClient(schedule, linescores)
    store = _FakeStore()
    backfill_fullgame_actuals(
        "2026-04-01", "2026-04-01", store, client=client,
    )
    row = store.upserts[0][1][0]
    assert row["home_runs"] == 3
    assert row["away_runs"] == 3
    assert row["f5_home_runs"] == 2
    assert row["f5_away_runs"] == 3
