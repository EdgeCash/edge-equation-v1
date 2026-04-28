"""NRFI training pipeline.

Phase 2 of the engine roadmap. Modules in this package own everything
between raw historical data and a trained, calibrated bundle ready for
the live daily run.

Layout (built incrementally across Phase 2a / 2b / 2c)::

    training/
    ├── __init__.py
    ├── backfill.py       (Phase 2a, this PR) — historical schedule +
    │                      lineups + first-inning actuals + Statcast
    │                      bulk pulls with DuckDB checkpointing.
    ├── walkforward.py    (Phase 2b)          — per-day train-on-D-1-and-back
    │                      / predict-on-D loop with rolling 18-month
    │                      window. Produces a final `TrainedBundle` plus
    │                      a calibration set spanning the full window.
    └── sanity.py         (Phase 2b)          — Brier / log-loss / accuracy
                           / ROI deltas vs the deterministic Poisson
                           baseline on 2026-to-date.

R2 upload + weekly cron land in Phase 2c (separate PR).
"""

from .backfill import (
    BackfillReport,
    BackfillResult,
    BACKFILL_OPS,
    backfill_range,
    init_checkpoint_table,
)

__all__ = [
    "BackfillReport",
    "BackfillResult",
    "BACKFILL_OPS",
    "backfill_range",
    "init_checkpoint_table",
]
