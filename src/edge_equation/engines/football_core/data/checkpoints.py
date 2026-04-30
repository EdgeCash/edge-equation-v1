"""Resumable checkpointing for chunked football backfills.

Pattern lifted from `engines/nrfi/training/backfill.py` so operators
crossing engines see one mental model:

* Each (sport, target_date, op) tuple is a unit of work. Ops are
  small enough that a single one finishing successfully is the
  granularity at which we resume.
* Successful units write a row to `football_backfill_checkpoints`
  with `error=NULL`.
* Failed units write the same row with `error=<message>` and
  `rows_loaded=0` so a retry sees the failure and tries again.
* `completed_pairs(store, sport)` returns the set the orchestrator
  uses to skip already-done work, making re-runs idempotent.

The backfill orchestrator wraps each loader call in a
`record_completion` (success) or `record_failure` (failure) so the
checkpoint table always reflects the actual state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class CheckpointResult:
    """Outcome of one (sport, date, op) unit."""
    sport: str
    target_date: str
    op: str
    success: bool
    rows_loaded: int = 0
    error: Optional[str] = None


def completed_pairs(store, sport: str) -> set[tuple[str, str]]:
    """Return the set of `(target_date, op)` pairs already completed
    successfully for `sport`. Failed rows are NOT included — caller
    will retry them.
    """
    df = store.query_df(
        """
        SELECT target_date, op
        FROM football_backfill_checkpoints
        WHERE sport = ? AND error IS NULL
        """,
        (sport,),
    )
    if df is None or df.empty:
        return set()
    return {
        (str(r.target_date), str(r.op))
        for _, r in df.iterrows()
    }


def record_completion(
    store, *, sport: str, target_date: str, op: str,
    rows_loaded: int = 0,
) -> None:
    """Mark a (sport, date, op) tuple as successfully completed.

    Idempotent — re-running overwrites the row (in case the operator
    re-pulled the same date for a fresh count).
    """
    store.upsert("football_backfill_checkpoints", [{
        "sport": sport,
        "target_date": target_date,
        "op": op,
        "completed_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ).replace("+00:00", ""),
        "rows_loaded": int(rows_loaded),
        "error": None,
    }])


def record_failure(
    store, *, sport: str, target_date: str, op: str, error: str,
) -> None:
    """Mark a (sport, date, op) tuple as failed. Stored with
    error=<message> so re-runs see it and retry."""
    store.upsert("football_backfill_checkpoints", [{
        "sport": sport,
        "target_date": target_date,
        "op": op,
        "completed_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ).replace("+00:00", ""),
        "rows_loaded": 0,
        "error": str(error)[:500],   # truncate so duckdb VARCHAR is happy
    }])


def chunk_dates(
    start: str, end: str, *, chunk_days: int = 7,
) -> list[tuple[str, str]]:
    """Split `[start, end]` into `chunk_days`-sized windows.

    Used by the orchestrator to feed week-sized chunks into the
    play-by-play loader (which is the heaviest op). Chunks include
    both endpoints; the last chunk may be shorter.
    """
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    if e < s:
        return []
    out: list[tuple[str, str]] = []
    cur = s
    from datetime import timedelta
    while cur <= e:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), e)
        out.append((cur.isoformat(), chunk_end.isoformat()))
        cur = chunk_end + timedelta(days=1)
    return out
