"""Tests for the Phase 3 data-facing NRFI backfill wrapper."""

from __future__ import annotations


def test_data_backfill_delegates_and_captures_odds(monkeypatch):
    from edge_equation.engines.nrfi.data import backfill as mod
    from edge_equation.engines.nrfi.training.backfill import BackfillReport

    calls = {"range": None, "odds": []}

    def _fake_range(*args, **kwargs):
        calls["range"] = (args, kwargs)
        report = BackfillReport()
        return report

    def _fake_capture(store, game_date, *, config=None):
        calls["odds"].append(game_date)
        return 2

    class _Cfg:
        def resolve_paths(self):
            return self

    class _Store:
        pass

    monkeypatch.setattr(mod, "backfill_range", _fake_range)
    monkeypatch.setattr(mod, "capture_closing_lines", _fake_capture)

    report = mod.backfill_historical_data(
        "2025-04-01",
        "2025-04-03",
        store=_Store(),
        config=_Cfg(),
        ops=("schedule", "actuals", "features"),
        max_days_per_run=2,
    )

    assert calls["range"][0][:2] == ("2025-04-01", "2025-04-03")
    assert calls["range"][1]["ops"] == ("schedule", "actuals", "features")
    assert calls["odds"] == ["2025-04-01", "2025-04-02"]
    assert report.odds_snapshots == 4
    assert "NRFI historical data backfill" in report.summary()


def test_data_backfill_can_skip_odds(monkeypatch):
    from edge_equation.engines.nrfi.data import backfill as mod
    from edge_equation.engines.nrfi.training.backfill import BackfillReport

    monkeypatch.setattr(mod, "backfill_range", lambda *a, **kw: BackfillReport())

    class _Cfg:
        def resolve_paths(self):
            return self

    report = mod.backfill_historical_data(
        "2025-04-01",
        "2025-04-01",
        store=object(),
        config=_Cfg(),
        include_odds=False,
    )

    assert report.odds_results == []
