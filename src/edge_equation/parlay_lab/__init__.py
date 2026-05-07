"""Parlay-engine shootout harness.

A pluggable framework for testing different parlay-construction
strategies against historical backfill data. Each engine implements
the same `build(legs, config)` contract and gets scored on the same
slate of historical bets via walk-forward backtesting.

The point: settle "ILP vs heuristic vs greedy" arguments with real
ROI numbers, calibration plots, and per-day hit rates instead of
intuition. See ``shootout.py`` for the orchestrator + CLI.

Phase 1 ships:

  - ParlayEngine ABC          (base.py)
  - BaselineEngine            (engines/baseline.py)
  - SameGameDedupedEngine     (engines/deduped.py)
  - Walk-forward iterator     (backfill.py)
  - ROI / hit-rate / calibration metrics (metrics.py)
  - Markdown leaderboard      (report.py)
  - CLI                       (shootout.py)

Subsequent phases add ILP, beam, independence-vs-copula engines.

Honest caveats baked in:

  * The MLB backtest's bet rows don't store the historical American
    odds line; we derive decimal_odds from the ``units`` field on
    WIN rows and fall back to -110 on LOSS / PUSH. ROI comparisons
    between engines are therefore unbiased (every engine sees the
    same odds), but absolute ROI numbers under-count winners that
    were +money lines.
  * Walk-forward isn't time-leak-proof in this version --- we use
    *all* of backtest.json's per-day data without re-running the
    engine's own model from scratch. The point is to compare
    PARLAY-CONSTRUCTION strategies against the same engine output,
    not to re-validate the underlying singles model.
"""
