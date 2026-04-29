"""Production historical data backfill for the NRFI engine.

This is the data-facing entry point requested for Phase 3.  It delegates the
heavy lifting to the existing checkpointed training backfill module, then adds
an optional best-effort Odds API capture pass so a single command prepares the
DuckDB store for training, calibration, daily Kelly suggestions, and ledger
settlement.

Data sources used by the delegated pipeline:

* MLB Stats API: schedule, probable pitchers, lineups, umpires, first-inning
  outcomes.
* Open-Meteo: archive/forecast weather during feature reconstruction.
* pybaseball/Statcast: recent first-inning pitcher context.
* The Odds API: available NRFI/YRFI 0.5 market snapshots.  Historical snapshots
  depend on provider plan/support; failures are logged and do not block model
  training because market odds are not required to fit probabilities.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

from edge_equation.utils.logging import get_logger

from ..config import NRFIConfig, get_default_config
from ..training.backfill import (
    BACKFILL_OPS,
    BackfillReport,
    BackfillResult,
    backfill_range,
)
from .odds import capture_closing_lines
from .storage import NRFIStore

log = get_logger(__name__)

DEFAULT_START_DATE = "2025-01-01"


@dataclass(frozen=True)
class OddsBackfillResult:
    """Outcome of one best-effort odds snapshot attempt."""

    target_date: str
    snapshots: int = 0
    success: bool = True
    error: Optional[str] = None


@dataclass
class HistoricalBackfillReport:
    """Roll-up returned by :func:`backfill_historical_data`."""

    start_date: str
    end_date: str
    data_report: BackfillReport
    odds_results: list[OddsBackfillResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def odds_snapshots(self) -> int:
        return sum(r.snapshots for r in self.odds_results if r.success)

    @property
    def odds_failures(self) -> int:
        return sum(1 for r in self.odds_results if not r.success)

    def summary(self) -> str:
        lines = [
            "NRFI historical data backfill",
            "-" * 56,
            f"  window              {self.start_date}..{self.end_date}",
            f"  data successes      {len(self.data_report.successes)}",
            f"  data failures       {self.data_report.n_failures}",
            f"  odds snapshots      {self.odds_snapshots}",
            f"  odds failures       {self.odds_failures}",
            f"  elapsed             {self.elapsed_seconds:.1f}s",
            "",
            self.data_report.summary(),
        ]
        return "\n".join(lines)


def backfill_historical_data(
    start_date: str = DEFAULT_START_DATE,
    end_date: Optional[str] = None,
    *,
    store: Optional[NRFIStore] = None,
    config: Optional[NRFIConfig] = None,
    ops: Sequence[str] = BACKFILL_OPS,
    include_odds: bool = True,
    skip_completed: bool = True,
    max_days_per_run: Optional[int] = None,
    statcast_window_days: int = 30,
    progress_callback=None,
) -> HistoricalBackfillReport:
    """Backfill features + actual NRFI outcomes into DuckDB.

    The function is resumable through the delegated checkpoint table.  It is
    safe to run repeatedly; completed operations are skipped by default.
    """

    cfg = (config or get_default_config()).resolve_paths()
    store = store or NRFIStore(cfg.duckdb_path)
    end = end_date or date.today().isoformat()
    started = time.monotonic()

    data_report = backfill_range(
        start_date,
        end,
        store=store,
        config=cfg,
        ops=ops,
        skip_completed=skip_completed,
        max_days_per_run=max_days_per_run,
        statcast_window_days=statcast_window_days,
        progress_callback=progress_callback,
    )
    odds_results: list[OddsBackfillResult] = []
    if include_odds:
        odds_results = _backfill_odds_snapshots(
            start_date,
            end,
            store=store,
            config=cfg,
            max_days=max_days_per_run,
        )

    return HistoricalBackfillReport(
        start_date=start_date,
        end_date=end,
        data_report=data_report,
        odds_results=odds_results,
        elapsed_seconds=time.monotonic() - started,
    )


def _backfill_odds_snapshots(
    start_date: str,
    end_date: str,
    *,
    store: NRFIStore,
    config: NRFIConfig,
    max_days: Optional[int] = None,
) -> list[OddsBackfillResult]:
    """Best-effort The Odds API capture across the requested dates."""

    out: list[OddsBackfillResult] = []
    cur = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    attempted = 0
    while cur <= end:
        if max_days is not None and attempted >= max_days:
            break
        ds = cur.isoformat()
        try:
            n = capture_closing_lines(store, ds, config=config)
            out.append(OddsBackfillResult(target_date=ds, snapshots=int(n)))
        except Exception as exc:  # defensive: capture should already swallow
            log.warning("odds backfill failed for %s: %s", ds, exc)
            out.append(OddsBackfillResult(
                target_date=ds,
                success=False,
                error=str(exc),
            ))
        cur += timedelta(days=1)
        attempted += 1
    return out


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill NRFI historical features, actuals, and odds."
    )
    parser.add_argument("--from", dest="start_date", default=DEFAULT_START_DATE)
    parser.add_argument("--to", dest="end_date", default=date.today().isoformat())
    parser.add_argument("--ops", default=",".join(BACKFILL_OPS))
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--statcast-window-days", type=int, default=30)
    parser.add_argument("--no-odds", action="store_true")
    parser.add_argument("--no-skip-completed", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    ops = tuple(o.strip() for o in args.ops.split(",") if o.strip())

    def _progress(result: BackfillResult) -> None:
        if args.quiet:
            return
        tag = "SKIP" if result.skipped else ("OK" if result.success else "FAIL")
        suffix = f" n={result.games_count}" if result.games_count else ""
        suffix += f" err={result.error}" if result.error else ""
        print(f"  {tag:<4} {result.target_date} {result.op:<9}{suffix}")

    report = backfill_historical_data(
        start_date=args.start_date,
        end_date=args.end_date,
        ops=ops,
        include_odds=not args.no_odds,
        skip_completed=not args.no_skip_completed,
        max_days_per_run=args.max_days,
        statcast_window_days=args.statcast_window_days,
        progress_callback=_progress,
    )
    print(report.summary())
    return 0 if report.data_report.n_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
