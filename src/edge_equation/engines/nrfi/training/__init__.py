"""NRFI training pipeline.

Phase 2 of the engine roadmap. Modules in this package own everything
between raw historical data and a trained, calibrated bundle ready for
the live daily run.

Layout::

    training/
    ├── __init__.py
    ├── backfill.py       (Phase 2a)  — historical schedule + lineups +
    │                      first-inning actuals + Statcast bulk pulls with
    │                      DuckDB checkpointing.
    ├── walkforward.py    (Phase 2b)  — per-chunk train-on-D-1-and-back /
    │                      predict-on-D loop with rolling 18-month window.
    │                      Produces a final TrainedBundle plus a
    │                      walk-forward calibration set.
    ├── sanity.py         (Phase 2b)  — Brier / log-loss / accuracy / ROI
    │                      deltas of the trained bundle vs the Poisson
    │                      baseline on 2026-to-date games.
    └── (R2 upload + weekly cron land in Phase 2c, separate PR)
"""

from .backfill import (
    BackfillReport,
    BackfillResult,
    BACKFILL_OPS,
    backfill_range,
    init_checkpoint_table,
)
from .walkforward import (
    ChunkResult,
    WalkForwardReport,
    walkforward_train,
    load_corpus,
)
from .sanity import (
    SanityReport,
    SanityRow,
    compute_sanity,
)

__all__ = [
    # Phase 2a
    "BackfillReport",
    "BackfillResult",
    "BACKFILL_OPS",
    "backfill_range",
    "init_checkpoint_table",
    # Phase 2b
    "ChunkResult",
    "WalkForwardReport",
    "walkforward_train",
    "load_corpus",
    "SanityReport",
    "SanityRow",
    "compute_sanity",
]
