"""Tests for the force-rebuild-features module.

Mocks `reconstruct_features_for_date` so we don't need DuckDB or
Statcast. Verifies the date enumeration, per-date dispatch, and
failure isolation.
"""

from __future__ import annotations

import pytest


pytest.importorskip("pandas")


class _FakeStore:
    def __init__(self, dates):
        self._dates = dates
        self.queries: list[str] = []

    def query_df(self, sql, params=None):
        import pandas as pd
        self.queries.append(sql)
        return pd.DataFrame([{"game_date": d} for d in self._dates])


def test_distinct_game_dates_returns_strings():
    from edge_equation.engines.nrfi.data.force_rebuild_features import (
        _distinct_game_dates,
    )
    store = _FakeStore(["2025-04-01", "2025-04-02"])
    out = _distinct_game_dates(store, start_date=None, end_date=None)
    assert out == ["2025-04-01", "2025-04-02"]


def test_distinct_game_dates_passes_window_params():
    from edge_equation.engines.nrfi.data.force_rebuild_features import (
        _distinct_game_dates,
    )
    store = _FakeStore(["2025-04-15"])
    _distinct_game_dates(
        store, start_date="2025-04-01", end_date="2025-04-30",
    )
    sql = store.queries[0]
    assert "game_date >= ?" in sql
    assert "game_date <= ?" in sql


def test_force_rebuild_dispatches_per_date(monkeypatch):
    from edge_equation.engines.nrfi.data import force_rebuild_features as mod

    seen_dates: list[str] = []

    def _fake_reconstruct(target_date, **_):
        seen_dates.append(target_date)
        return [(1, {"feat_a": 1.0}), (2, {"feat_a": 2.0})]

    store = _FakeStore(["2025-04-01", "2025-04-02", "2025-04-03"])
    monkeypatch.setattr(mod, "reconstruct_features_for_date", _fake_reconstruct)

    report = mod.force_rebuild_features(store=store)
    assert seen_dates == ["2025-04-01", "2025-04-02", "2025-04-03"]
    assert report.n_dates_rebuilt == 3
    assert report.n_features_written == 6   # 2 games per date × 3 dates


def test_force_rebuild_isolates_failures(monkeypatch):
    """One bad date doesn't kill the whole run — caller can re-run
    just the failed dates after fixing the underlying issue."""
    from edge_equation.engines.nrfi.data import force_rebuild_features as mod

    def _fake_reconstruct(target_date, **_):
        if target_date == "2025-04-02":
            raise RuntimeError("simulated statcast 500")
        return [(1, {"x": 1.0})]

    store = _FakeStore(["2025-04-01", "2025-04-02", "2025-04-03"])
    monkeypatch.setattr(mod, "reconstruct_features_for_date", _fake_reconstruct)

    report = mod.force_rebuild_features(store=store)
    assert report.n_dates_rebuilt == 2
    assert report.n_dates_failed == 1
    assert any("2025-04-02" in e for e in report.errors)


def test_force_rebuild_window_filter(monkeypatch):
    from edge_equation.engines.nrfi.data import force_rebuild_features as mod

    seen_dates: list[str] = []

    def _fake_reconstruct(target_date, **_):
        seen_dates.append(target_date)
        return []

    # Store will return whatever dates the SQL yields; the actual
    # window filtering happens in SQL, so just verify the params get
    # threaded through. With our fake, the dates list is the same.
    store = _FakeStore(["2025-04-15"])
    monkeypatch.setattr(mod, "reconstruct_features_for_date", _fake_reconstruct)

    report = mod.force_rebuild_features(
        store=store, start_date="2025-04-01", end_date="2025-04-30",
    )
    assert report.n_dates_total == 1
    assert seen_dates == ["2025-04-15"]
