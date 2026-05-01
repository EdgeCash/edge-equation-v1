"""Rebuild every feature blob in the corpus from current code.

PR #104 added new feature columns (`bottom3_obp`, `lineup_shape_obp_gap`,
`int_park_hr_x_lhh_skew_*`, etc.) to `feature_engineering.py`. The
trainer reads `feature_blob` JSON from DuckDB; existing blobs were
frozen before #104 merged and don't carry the new columns. The
trainer therefore can't see them — every walkforward run produces
the same predictions until features are rewritten.

This module fixes that. It walks every distinct game_date in the
games table and re-runs ``reconstruct_features_for_date`` per date,
which calls ``store.upsert("features", ...)`` and overwrites the
existing blob with fresh JSON that includes the new columns. Side
effect: weather rows for those dates also get re-persisted (the
reconstruct loop writes them via the PR #101 hook).

Idempotent. Safe to run repeatedly. Slow — re-runs the full Statcast
window per date, just like the original feature build.

CLI
~~~

::

    # Rebuild everything in the corpus
    python -m edge_equation.engines.nrfi.data.force_rebuild_features

    # Limit to a window
    python -m edge_equation.engines.nrfi.data.force_rebuild_features \\
        --from 2026-04-01 --to 2026-04-30
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

from edge_equation.utils.logging import get_logger

from ..config import NRFIConfig, get_default_config
from ..evaluation.backtest import reconstruct_features_for_date
from .storage import NRFIStore

log = get_logger(__name__)


@dataclass
class RebuildReport:
    n_dates_total: int = 0
    n_dates_rebuilt: int = 0
    n_dates_failed: int = 0
    n_features_written: int = 0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "NRFI feature force-rebuild",
            "-" * 56,
            f"  dates in window           {self.n_dates_total}",
            f"  dates rebuilt             {self.n_dates_rebuilt}",
            f"  dates failed              {self.n_dates_failed}",
            f"  feature rows written      {self.n_features_written}",
            f"  elapsed                   {self.elapsed_s:.1f}s",
        ]
        if self.errors:
            lines.append("  recent errors:")
            for e in self.errors[:5]:
                lines.append(f"    {e[:120]}")
        return "\n".join(lines)


def force_rebuild_features(
    *,
    store: Optional[NRFIStore] = None,
    config: Optional[NRFIConfig] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    statcast_window_days: int = 30,
    progress_callback=None,
) -> RebuildReport:
    """Walk every distinct game_date in the corpus and re-run
    ``reconstruct_features_for_date`` to rewrite the feature blob with
    current code's columns.

    Parameters
    ----------
    statcast_window_days : Forwarded to reconstruct. 30 matches the
        production default; widen to 60-90 for low-PA early-season
        starters when needed.
    """
    cfg = (config or get_default_config()).resolve_paths()
    store = store or NRFIStore(cfg.duckdb_path)

    report = RebuildReport()
    started = time.monotonic()

    dates = _distinct_game_dates(
        store, start_date=start_date, end_date=end_date,
    )
    report.n_dates_total = len(dates)

    for i, ds in enumerate(dates):
        if progress_callback is not None:
            progress_callback(i, len(dates), ds)
        try:
            feats = reconstruct_features_for_date(
                ds, store=store, config=cfg,
                statcast_window_days=statcast_window_days,
            )
            report.n_dates_rebuilt += 1
            report.n_features_written += len(feats)
        except Exception as e:
            report.n_dates_failed += 1
            report.errors.append(f"{ds}: {e}")

    report.elapsed_s = time.monotonic() - started
    return report


def _distinct_game_dates(
    store: NRFIStore,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
) -> list[str]:
    where_clauses: list[str] = []
    params: list = []
    if start_date:
        where_clauses.append("game_date >= ?")
        params.append(start_date)
    if end_date:
        where_clauses.append("game_date <= ?")
        params.append(end_date)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    sql = f"""
        SELECT DISTINCT CAST(game_date AS VARCHAR) AS game_date
        FROM games
        {where_sql}
        ORDER BY game_date
    """
    df = store.query_df(sql, tuple(params))
    if df is None or df.empty:
        return []
    return [str(r["game_date"])[:10] for _, r in df.iterrows()]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Force-rebuild every NRFI feature blob with current code.",
    )
    parser.add_argument("--from", dest="start_date", default=None)
    parser.add_argument("--to", dest="end_date", default=None)
    parser.add_argument("--statcast-window-days", type=int, default=30)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    def _progress(i, total, ds):
        if args.quiet:
            return
        if total < 50 or i % max(1, total // 30) == 0:
            print(f"  [{i+1}/{total}] {ds}")

    report = force_rebuild_features(
        start_date=args.start_date,
        end_date=args.end_date,
        statcast_window_days=args.statcast_window_days,
        progress_callback=None if args.quiet else _progress,
    )
    print(report.summary())
    return 0 if report.n_dates_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
