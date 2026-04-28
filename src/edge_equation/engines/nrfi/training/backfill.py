"""Historical-data backfill orchestrator (Phase 2a).

The training pipeline (Phase 2b) needs ~600 days of consistent
schedule + lineups + first-inning runs + Statcast first-inning splits
to walk-forward train. This module is the one-shot loader that
populates the DuckDB store.

Design goals
------------

* **Resumable**. Every (date, op) pair gets a checkpoint row in
  ``backfill_checkpoints``. Re-running with the same date range
  fast-paths through anything already complete and only does the
  missing work.
* **Per-op granularity**. The three operations — `schedule` (calls
  `daily_etl`), `actuals` (calls `backfill_actuals` for first-inning
  runs), and `statcast` (chunked Statcast bulk pulls cached as parquet)
  — can be run independently, so a bad Statcast day doesn't block
  ingesting the schedule.
* **Failure isolation**. One day's failure is logged and the loop
  continues. The final report enumerates failures so the operator
  can re-run a narrow window once upstream is healthy again.
* **Cron-friendly**. ``max_days_per_run`` lets a CI cron chip away at
  the historical window across multiple short runs without ever
  exceeding the 6-hour GitHub Actions job timeout.

Statcast strategy
-----------------

pybaseball's ``statcast(start_dt, end_dt)`` fetches Baseball Savant's
public CSV in 1-day chunks under the hood and caches each chunk on
disk. We exploit that by issuing the call in 30-day windows — Savant
caches the per-day result so even if our wider window straddles
already-pulled days the bandwidth cost is zero. This dramatically
reduces total runtime compared to per-game pulls.

Checkpoint table schema
-----------------------

::

    CREATE TABLE IF NOT EXISTS backfill_checkpoints (
        target_date    DATE,
        op             VARCHAR,    -- 'schedule' | 'actuals' | 'statcast'
        completed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        games_count    INTEGER,
        notes          VARCHAR,
        PRIMARY KEY (target_date, op)
    );
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Iterable, Optional, Sequence

from edge_equation.utils.logging import get_logger

from ..config import NRFIConfig, get_default_config
from ..data.scrapers_etl import (
    backfill_actuals,
    daily_etl,
    fetch_statcast_first_inning,
)
from ..data.storage import NRFIStore

log = get_logger(__name__)


# Recognised operations. Order matters: schedule must run before
# actuals (the actuals pull joins against the games table).
BACKFILL_OPS: tuple[str, ...] = ("schedule", "actuals", "statcast")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackfillResult:
    """Outcome for a single (date, op) pair."""

    target_date: str
    op: str
    success: bool
    games_count: int = 0
    error: Optional[str] = None
    skipped: bool = False  # True when checkpoint said already done


@dataclass
class BackfillReport:
    """Roll-up across an entire backfill run."""

    n_dates_processed: int = 0
    n_dates_skipped: int = 0
    n_failures: int = 0
    failures: list[BackfillResult] = field(default_factory=list)
    successes: list[BackfillResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            "Backfill report",
            "─" * 50,
            f"  dates processed     {self.n_dates_processed}",
            f"  dates skipped       {self.n_dates_skipped}",
            f"  ops succeeded       {len(self.successes)}",
            f"  ops failed          {self.n_failures}",
            f"  elapsed             {self.elapsed_seconds:.1f}s",
        ]
        if self.failures:
            lines.append("  failures:")
            for f in self.failures[:20]:
                lines.append(f"    {f.target_date}  {f.op:<9}  {f.error}")
            if len(self.failures) > 20:
                lines.append(f"    ... +{len(self.failures) - 20} more")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Checkpoint table
# ---------------------------------------------------------------------------


_CHECKPOINT_DDL = """
CREATE TABLE IF NOT EXISTS backfill_checkpoints (
    target_date    DATE,
    op             VARCHAR,
    completed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    games_count    INTEGER,
    notes          VARCHAR,
    PRIMARY KEY (target_date, op)
)
"""


def init_checkpoint_table(store: NRFIStore) -> None:
    """Idempotent — safe to call on every backfill run."""
    store.execute(_CHECKPOINT_DDL)


def _completed_pairs(store: NRFIStore) -> set[tuple[str, str]]:
    """Return the set of (target_date_str, op) pairs already checkpointed."""
    df = store.query_df(
        "SELECT target_date, op FROM backfill_checkpoints"
    )
    if df is None or df.empty:
        return set()
    return {(str(d)[:10], str(o)) for d, o in zip(df.target_date, df.op)}


def _record_completion(store: NRFIStore, target_date: str, op: str,
                        games_count: int = 0, notes: str = "") -> None:
    store.upsert("backfill_checkpoints", [{
        "target_date": target_date,
        "op": op,
        "games_count": int(games_count),
        "notes": notes,
    }])


# ---------------------------------------------------------------------------
# Per-op runners
# ---------------------------------------------------------------------------


def _run_schedule_op(target_date: str, store: NRFIStore,
                      cfg: NRFIConfig) -> BackfillResult:
    try:
        n = daily_etl(target_date, store, config=cfg)
        _record_completion(store, target_date, "schedule",
                            games_count=int(n))
        return BackfillResult(target_date, "schedule", True, games_count=int(n))
    except Exception as e:
        return BackfillResult(target_date, "schedule", False, error=str(e))


def _run_actuals_op(target_date: str, store: NRFIStore,
                     cfg: NRFIConfig) -> BackfillResult:
    try:
        n = backfill_actuals(target_date, target_date, store, config=cfg)
        _record_completion(store, target_date, "actuals",
                            games_count=int(n))
        return BackfillResult(target_date, "actuals", True, games_count=int(n))
    except Exception as e:
        return BackfillResult(target_date, "actuals", False, error=str(e))


def _run_statcast_window(start_date: str, end_date: str, store: NRFIStore,
                          cfg: NRFIConfig) -> list[BackfillResult]:
    """Statcast is pulled in bulk windows, then we checkpoint each
    day in the window once the call returns. Failures inside the
    pybaseball call surface as a single failure for the whole window
    rather than per-day so the operator can retry the window.
    """
    try:
        df = fetch_statcast_first_inning(start_date, end_date, config=cfg)
        n = 0 if df is None else len(df)
    except Exception as e:
        return [
            BackfillResult(start_date, "statcast", False,
                            error=f"window {start_date}..{end_date}: {e}")
        ]

    out: list[BackfillResult] = []
    cur = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while cur <= end:
        d = cur.isoformat()
        _record_completion(store, d, "statcast",
                            games_count=int(n) // max(1, (end - date.fromisoformat(start_date)).days + 1),
                            notes=f"window {start_date}..{end_date}")
        out.append(BackfillResult(d, "statcast", True))
        cur += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def backfill_range(
    start_date: str,
    end_date: str,
    *,
    store: Optional[NRFIStore] = None,
    config: Optional[NRFIConfig] = None,
    ops: Sequence[str] = BACKFILL_OPS,
    skip_completed: bool = True,
    max_days_per_run: Optional[int] = None,
    statcast_window_days: int = 30,
    progress_callback: Optional[Callable[[BackfillResult], None]] = None,
) -> BackfillReport:
    """Walk [start_date, end_date] and run each requested op per day.

    Parameters
    ----------
    start_date, end_date : ISO date strings (inclusive).
    store : Open `NRFIStore`. Constructed from default config if omitted.
    ops : Subset of `BACKFILL_OPS`. Default runs all three.
    skip_completed : When True (default) consult the checkpoint table
        and fast-path through (date, op) pairs already done.
    max_days_per_run : Soft cap so a single CI invocation doesn't blow
        past the GH Actions 6-hour timeout. Returns early once we've
        *processed* (i.e. attempted at least one op for) this many
        distinct dates. The next run picks up where we left off via
        the checkpoint table.
    statcast_window_days : Bulk-pull window. 30 days is the sweet spot:
        wide enough to amortise the per-call overhead, narrow enough
        that one bad day doesn't poison a quarter of the season.
    progress_callback : Optional sink — receives every BackfillResult
        as it lands. Used by the CLI for live progress; tests pass a
        list.append for assertions.
    """
    cfg = (config or get_default_config()).resolve_paths()
    store = store or NRFIStore(cfg.duckdb_path)
    init_checkpoint_table(store)

    report = BackfillReport()
    started = time.monotonic()
    bad_ops = [o for o in ops if o not in BACKFILL_OPS]
    if bad_ops:
        raise ValueError(f"Unknown ops: {bad_ops}; valid: {BACKFILL_OPS}")

    # Resolve already-completed pairs once up front.
    done = _completed_pairs(store) if skip_completed else set()

    # Build the date sequence.
    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date)
    if d0 > d1:
        raise ValueError(f"start_date {start_date} > end_date {end_date}")
    dates: list[date] = []
    cur = d0
    while cur <= d1:
        dates.append(cur)
        cur += timedelta(days=1)

    distinct_dates_processed = 0

    # Pass 1: schedule + actuals (per-day) ---------------------------------
    for d in dates:
        ds = d.isoformat()
        date_processed = False

        if "schedule" in ops:
            if (ds, "schedule") in done:
                report.n_dates_skipped += 1
                if progress_callback:
                    progress_callback(BackfillResult(ds, "schedule", True, skipped=True))
            else:
                res = _run_schedule_op(ds, store, cfg)
                _record_outcome(report, res)
                if progress_callback:
                    progress_callback(res)
                date_processed = True

        if "actuals" in ops:
            if (ds, "actuals") in done:
                if progress_callback:
                    progress_callback(BackfillResult(ds, "actuals", True, skipped=True))
            else:
                res = _run_actuals_op(ds, store, cfg)
                _record_outcome(report, res)
                if progress_callback:
                    progress_callback(res)
                date_processed = True

        if date_processed:
            distinct_dates_processed += 1
            report.n_dates_processed += 1

        if max_days_per_run is not None and distinct_dates_processed >= max_days_per_run:
            log.info("Reached max_days_per_run=%d — stopping early; "
                     "re-run to continue.", max_days_per_run)
            report.elapsed_seconds = time.monotonic() - started
            return report

    # Pass 2: statcast (bulk windows) --------------------------------------
    if "statcast" in ops:
        cur_d = d0
        while cur_d <= d1:
            window_end = min(cur_d + timedelta(days=statcast_window_days - 1), d1)
            ds = cur_d.isoformat()
            we = window_end.isoformat()

            # Only call pybaseball if at least one day in the window
            # isn't already checkpointed.
            window_dates = []
            check = cur_d
            while check <= window_end:
                window_dates.append(check.isoformat())
                check += timedelta(days=1)
            unfinished = [d for d in window_dates if (d, "statcast") not in done]
            if not unfinished:
                for d in window_dates:
                    if progress_callback:
                        progress_callback(BackfillResult(d, "statcast", True, skipped=True))
                cur_d = window_end + timedelta(days=1)
                continue

            results = _run_statcast_window(ds, we, store, cfg)
            for r in results:
                _record_outcome(report, r)
                if progress_callback:
                    progress_callback(r)
            cur_d = window_end + timedelta(days=1)

    report.elapsed_seconds = time.monotonic() - started
    return report


def _record_outcome(report: BackfillReport, res: BackfillResult) -> None:
    if res.skipped:
        report.n_dates_skipped += 1
        return
    if res.success:
        report.successes.append(res)
    else:
        report.failures.append(res)
        report.n_failures += 1
        log.warning("backfill failed: %s op=%s error=%s",
                     res.target_date, res.op, res.error)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    import argparse
    today = date.today()
    parser = argparse.ArgumentParser(
        description="NRFI historical data backfill orchestrator (Phase 2a)"
    )
    parser.add_argument(
        "--from", dest="start_date", default="2024-09-01",
        help="Inclusive start date (default: 2024-09-01 — the rolling 18-month "
              "window's lower bound).",
    )
    parser.add_argument(
        "--to", dest="end_date", default=today.isoformat(),
        help="Inclusive end date (default: today UTC).",
    )
    parser.add_argument(
        "--ops", default=",".join(BACKFILL_OPS),
        help=f"Comma-separated ops to run; subset of {BACKFILL_OPS}",
    )
    parser.add_argument(
        "--max-days", type=int, default=None,
        help="Soft cap on distinct dates processed before returning. "
              "Used by the cron to chip away at history across multiple runs.",
    )
    parser.add_argument(
        "--no-skip-completed", action="store_true",
        help="Re-run ops even if checkpointed. Default: skip already-done.",
    )
    parser.add_argument(
        "--statcast-window-days", type=int, default=30,
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-day progress logging (still prints final summary).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    ops = tuple(o.strip() for o in args.ops.split(",") if o.strip())

    def _on_progress(r: BackfillResult) -> None:
        if args.quiet:
            return
        tag = "SKIP" if r.skipped else ("OK  " if r.success else "FAIL")
        suffix = f" n={r.games_count}" if r.success and r.games_count else ""
        suffix += f"  err={r.error}" if r.error else ""
        print(f"  {tag}  {r.target_date}  {r.op:<9}{suffix}")

    cfg = get_default_config().resolve_paths()
    store = NRFIStore(cfg.duckdb_path)
    log.info("backfill: %s..%s ops=%s max_days=%s",
             args.start_date, args.end_date, ops, args.max_days)
    report = backfill_range(
        args.start_date, args.end_date,
        store=store, config=cfg, ops=ops,
        skip_completed=not args.no_skip_completed,
        max_days_per_run=args.max_days,
        statcast_window_days=args.statcast_window_days,
        progress_callback=_on_progress,
    )
    print()
    print(report.summary())
    return 0 if report.n_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
