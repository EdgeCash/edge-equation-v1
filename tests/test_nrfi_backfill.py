"""Tests for the NRFI historical backfill orchestrator (Phase 2a).

Pure-Python — no DuckDB, no pybaseball, no network. The store and the
three op runners are monkey-patched so we can drive failure / success
patterns deterministically and verify checkpointing, failure
isolation, skip-completed, and the max_days_per_run cap.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from edge_equation.engines.nrfi.training import backfill as bf


# ---------------------------------------------------------------------------
# Fake store — minimal subset of NRFIStore the backfill module touches
# ---------------------------------------------------------------------------

class _FakeNRFIStore:
    """In-memory fake. Mirrors only the methods backfill.py calls."""

    def __init__(self):
        self.checkpoints: list[dict] = []
        self.executed: list[str] = []
        # Allow tests to customise what query_df returns.
        self._query_responses: dict[str, object] = {}

    def execute(self, sql: str, params=None) -> None:
        self.executed.append(sql.strip())

    def upsert(self, table: str, rows: list[dict]) -> int:
        if table != "backfill_checkpoints":
            raise AssertionError(f"unexpected upsert into {table!r}")
        for r in rows:
            # Replace if matching (target_date, op) already present.
            self.checkpoints = [
                c for c in self.checkpoints
                if not (c["target_date"] == r["target_date"]
                        and c["op"] == r["op"])
            ]
            self.checkpoints.append(dict(r))
        return len(rows)

    def query_df(self, sql: str, params=None):
        # The backfill module only queries `SELECT target_date, op FROM
        # backfill_checkpoints`. Build a tiny duck-typed frame.
        if "FROM backfill_checkpoints" in sql:
            return _FakeDataFrame(self.checkpoints)
        return self._query_responses.get(sql, _FakeDataFrame([]))


class _FakeDataFrame:
    """Just-enough pandas surface for backfill._completed_pairs."""

    def __init__(self, rows: list[dict]):
        self._rows = list(rows)
        self.target_date = [r["target_date"] for r in self._rows]
        self.op = [r["op"] for r in self._rows]
        self.empty = len(self._rows) == 0

    def __len__(self) -> int:
        return len(self._rows)


# ---------------------------------------------------------------------------
# Op runner stubs — patched onto the module
# ---------------------------------------------------------------------------

def _stub_daily_etl(target_date: str, store, *, config=None) -> int:
    """Default: succeeds with 8 games. Tests override per-date via
    `_FAILING_DATES` set on the module under monkeypatch."""
    if target_date in _FAILING_DATES.get("schedule", set()):
        raise RuntimeError(f"simulated schedule failure for {target_date}")
    return _GAMES_PER_DATE.get(target_date, 8)


def _stub_backfill_actuals(start, end, store, *, config=None) -> int:
    if start in _FAILING_DATES.get("actuals", set()):
        raise RuntimeError(f"simulated actuals failure for {start}")
    return _GAMES_PER_DATE.get(start, 8)


def _stub_fetch_statcast(start_date, end_date, *, config=None):
    if (start_date, end_date) in _FAILING_DATES.get("statcast", set()):
        raise RuntimeError(f"simulated statcast failure for {start_date}..{end_date}")
    # Return a small list-shaped mock that has __len__.
    return _FakeDataFrame([{"target_date": start_date, "op": "stub"}])


def _stub_run_features_op(target_date, store, cfg):
    """Replace the features op runner directly so we don't have to
    import the heavy reconstruct_features_for_date pipeline in tests."""
    if target_date in _FAILING_DATES.get("features", set()):
        return bf.BackfillResult(target_date, "features", False,
                                   error="simulated features failure")
    n = _GAMES_PER_DATE.get(target_date, 8)
    bf._record_completion(store, target_date, "features", games_count=n)
    return bf.BackfillResult(target_date, "features", True, games_count=n)


# Test-controlled state mutated per-test via monkeypatch.
_FAILING_DATES: dict[str, set[str]] = {}
_GAMES_PER_DATE: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _wire_stubs(monkeypatch):
    # Reset and stub each op runner.
    _FAILING_DATES.clear()
    _GAMES_PER_DATE.clear()
    monkeypatch.setattr(bf, "daily_etl", _stub_daily_etl)
    monkeypatch.setattr(bf, "backfill_actuals", _stub_backfill_actuals)
    monkeypatch.setattr(bf, "fetch_statcast_first_inning", _stub_fetch_statcast)
    monkeypatch.setattr(bf, "_run_features_op", _stub_run_features_op)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_backfill_three_day_range_all_ops():
    store = _FakeNRFIStore()
    report = bf.backfill_range(
        "2024-09-01", "2024-09-03",
        store=store, ops=("schedule", "actuals", "statcast", "features"),
    )
    assert report.n_failures == 0
    # 3 days × 3 per-day ops (schedule, actuals, features) = 9 successes;
    # statcast bulk window covers all 3 dates and adds 3 more.
    assert len(report.successes) == 12
    # Every (date, op) checkpointed.
    pairs = {(c["target_date"], c["op"]) for c in store.checkpoints}
    expected = set()
    for d in ("2024-09-01", "2024-09-02", "2024-09-03"):
        for op in ("schedule", "actuals", "statcast", "features"):
            expected.add((d, op))
    assert pairs == expected


def test_features_op_in_BACKFILL_OPS():
    assert "features" in bf.BACKFILL_OPS
    # Schedule, actuals, statcast, features — in that order.
    assert bf.BACKFILL_OPS == ("schedule", "actuals", "statcast", "features")


def test_features_op_runs_per_day_and_checkpoints():
    """The features op runs as Pass 3, once per day, after schedule
    and statcast. Each day gets its own checkpoint row."""
    store = _FakeNRFIStore()
    bf.backfill_range("2024-09-01", "2024-09-03",
                       store=store, ops=("features",))
    pairs = {(c["target_date"], c["op"]) for c in store.checkpoints}
    assert pairs == {
        ("2024-09-01", "features"),
        ("2024-09-02", "features"),
        ("2024-09-03", "features"),
    }


def test_features_op_failure_does_not_kill_run():
    _FAILING_DATES["features"] = {"2024-09-02"}
    store = _FakeNRFIStore()
    report = bf.backfill_range("2024-09-01", "2024-09-03",
                                 store=store, ops=("features",))
    assert report.n_failures == 1
    assert report.failures[0].target_date == "2024-09-02"
    pairs = {(c["target_date"], c["op"]) for c in store.checkpoints}
    assert ("2024-09-01", "features") in pairs
    assert ("2024-09-03", "features") in pairs
    assert ("2024-09-02", "features") not in pairs


def test_features_op_skips_completed():
    store = _FakeNRFIStore()
    # First run.
    bf.backfill_range("2024-09-01", "2024-09-02",
                       store=store, ops=("features",))
    # Second run — should skip both already-checkpointed dates.
    progress: list = []
    bf.backfill_range("2024-09-01", "2024-09-02",
                       store=store, ops=("features",),
                       progress_callback=progress.append)
    assert all(p.skipped for p in progress)
    assert len(progress) == 2


def test_backfill_init_creates_checkpoint_table():
    store = _FakeNRFIStore()
    bf.init_checkpoint_table(store)
    assert any("CREATE TABLE IF NOT EXISTS backfill_checkpoints"
               in s for s in store.executed)


# ---------------------------------------------------------------------------
# Resumability — second run skips already-completed
# ---------------------------------------------------------------------------

def test_backfill_skips_completed_on_rerun():
    store = _FakeNRFIStore()

    # First run.
    r1 = bf.backfill_range("2024-09-01", "2024-09-02",
                            store=store, ops=("schedule", "actuals"))
    assert r1.n_failures == 0
    n_checkpoints_after_first = len(store.checkpoints)

    # Second run on identical range — every (date, op) must be skipped.
    progress: list[bf.BackfillResult] = []
    r2 = bf.backfill_range("2024-09-01", "2024-09-02",
                            store=store, ops=("schedule", "actuals"),
                            progress_callback=progress.append)
    # No new checkpoints (no work done).
    assert len(store.checkpoints) == n_checkpoints_after_first
    # No new successes / failures recorded.
    assert r2.n_failures == 0
    assert len(r2.successes) == 0
    # Every progress event was a skip.
    assert all(p.skipped for p in progress)
    assert len(progress) == 4   # 2 dates × 2 ops


def test_backfill_no_skip_completed_reruns_everything():
    store = _FakeNRFIStore()
    bf.backfill_range("2024-09-01", "2024-09-01",
                       store=store, ops=("schedule",))
    r2 = bf.backfill_range("2024-09-01", "2024-09-01",
                            store=store, ops=("schedule",),
                            skip_completed=False)
    # Re-running with skip_completed=False produces a fresh success.
    assert len(r2.successes) == 1


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

def test_one_bad_day_does_not_kill_the_run():
    _FAILING_DATES["schedule"] = {"2024-09-02"}
    store = _FakeNRFIStore()

    report = bf.backfill_range("2024-09-01", "2024-09-03",
                                 store=store, ops=("schedule",))
    assert report.n_failures == 1
    assert report.failures[0].target_date == "2024-09-02"
    assert "simulated" in (report.failures[0].error or "")
    # The other two days still ran and got checkpointed.
    pairs = {(c["target_date"], c["op"]) for c in store.checkpoints}
    assert ("2024-09-01", "schedule") in pairs
    assert ("2024-09-03", "schedule") in pairs
    assert ("2024-09-02", "schedule") not in pairs


def test_actuals_failure_does_not_block_schedule_for_same_day():
    _FAILING_DATES["actuals"] = {"2024-09-01"}
    store = _FakeNRFIStore()

    report = bf.backfill_range("2024-09-01", "2024-09-01",
                                 store=store, ops=("schedule", "actuals"))
    pairs = {(c["target_date"], c["op"]) for c in store.checkpoints}
    assert ("2024-09-01", "schedule") in pairs
    assert ("2024-09-01", "actuals") not in pairs
    assert report.n_failures == 1


# ---------------------------------------------------------------------------
# max_days_per_run cap
# ---------------------------------------------------------------------------

def test_max_days_per_run_caps_distinct_dates_processed():
    store = _FakeNRFIStore()
    # 5-day range, cap at 2 distinct dates.
    report = bf.backfill_range(
        "2024-09-01", "2024-09-05",
        store=store, ops=("schedule",),
        max_days_per_run=2,
    )
    assert report.n_dates_processed == 2
    pairs = {(c["target_date"], c["op"]) for c in store.checkpoints}
    # Only the first two days were processed.
    assert pairs == {("2024-09-01", "schedule"), ("2024-09-02", "schedule")}


def test_max_days_per_run_resumes_on_subsequent_invocation():
    store = _FakeNRFIStore()
    # First chunk.
    bf.backfill_range("2024-09-01", "2024-09-04",
                       store=store, ops=("schedule",), max_days_per_run=2)
    # Second chunk — should pick up at day 3 (days 1+2 are checkpointed).
    bf.backfill_range("2024-09-01", "2024-09-04",
                       store=store, ops=("schedule",), max_days_per_run=2)
    pairs = {(c["target_date"], c["op"]) for c in store.checkpoints}
    assert pairs == {
        ("2024-09-01", "schedule"), ("2024-09-02", "schedule"),
        ("2024-09-03", "schedule"), ("2024-09-04", "schedule"),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_unknown_op_raises():
    store = _FakeNRFIStore()
    with pytest.raises(ValueError, match="Unknown ops"):
        bf.backfill_range("2024-09-01", "2024-09-01",
                          store=store, ops=("nonsense",))


def test_inverted_date_range_raises():
    store = _FakeNRFIStore()
    with pytest.raises(ValueError, match="start_date.*>.*end_date"):
        bf.backfill_range("2024-09-05", "2024-09-01", store=store)


# ---------------------------------------------------------------------------
# Statcast bulk-window behaviour
# ---------------------------------------------------------------------------

def test_statcast_bulk_window_checkpoints_every_day():
    store = _FakeNRFIStore()
    # 5-day range with a single 30-day window → one fetch call,
    # but every day in the range gets its own checkpoint row.
    bf.backfill_range("2024-09-01", "2024-09-05",
                       store=store, ops=("statcast",))
    statcast_dates = {c["target_date"] for c in store.checkpoints
                       if c["op"] == "statcast"}
    assert statcast_dates == {f"2024-09-0{d}" for d in range(1, 6)}


def test_statcast_window_failure_is_logged_per_window_not_per_day():
    _FAILING_DATES["statcast"] = {("2024-09-01", "2024-09-05")}
    store = _FakeNRFIStore()
    report = bf.backfill_range("2024-09-01", "2024-09-05",
                                 store=store, ops=("statcast",),
                                 statcast_window_days=30)
    # One failure for the window, no checkpoints written.
    assert report.n_failures == 1
    statcast_checks = [c for c in store.checkpoints if c["op"] == "statcast"]
    assert statcast_checks == []


def test_statcast_skips_already_completed_window():
    store = _FakeNRFIStore()
    # First run completes the full window.
    bf.backfill_range("2024-09-01", "2024-09-03",
                       store=store, ops=("statcast",))
    # Second run on identical window — must NOT call the fetcher again.
    fetch_calls: list[tuple] = []
    def _spy(*a, **kw):
        fetch_calls.append(a)
        return _FakeDataFrame([])
    import edge_equation.engines.nrfi.training.backfill as mod
    mod.fetch_statcast_first_inning = _spy
    try:
        bf.backfill_range("2024-09-01", "2024-09-03",
                           store=store, ops=("statcast",))
        assert fetch_calls == []  # no network hits
    finally:
        # Restore the autouse stub for next test.
        mod.fetch_statcast_first_inning = _stub_fetch_statcast


# ---------------------------------------------------------------------------
# Report summary string
# ---------------------------------------------------------------------------

def test_report_summary_includes_failure_lines():
    _FAILING_DATES["schedule"] = {"2024-09-02"}
    store = _FakeNRFIStore()
    report = bf.backfill_range("2024-09-01", "2024-09-03",
                                 store=store, ops=("schedule",))
    s = report.summary()
    assert "Backfill report" in s
    assert "ops failed" in s
    assert "2024-09-02" in s
    assert "schedule" in s


def test_report_summary_truncates_failure_list_at_20():
    _FAILING_DATES["schedule"] = {f"2024-09-{i:02d}" for i in range(1, 30)}
    store = _FakeNRFIStore()
    report = bf.backfill_range("2024-09-01", "2024-09-29",
                                 store=store, ops=("schedule",))
    s = report.summary()
    assert "+9 more" in s   # 29 failures, first 20 listed, +9 elided
