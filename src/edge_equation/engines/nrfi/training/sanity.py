"""Sanity report for the trained NRFI bundle (Phase 2b).

After walk-forward training produces a `TrainedBundle`, the next
question is *"is it actually better than the deterministic Poisson
baseline we already had?"*. This module answers that on 2026-to-date
games where we have ground-truth first-inning outcomes.

Output is a small dataclass + a printable table::

    Sanity report — ML bundle vs Poisson baseline
    ─────────────────────────────────────────────────────────────
                          ML bundle    Poisson    Delta
      n games                 412         412
      base NRFI rate          0.535       0.535
      accuracy@.5             0.621       0.572      +0.049
      brier                   0.2168      0.2334     -0.0166
      log loss                0.6342      0.6709     -0.0367
      ROI (flat 1u, edge≥4%)  +18.4u      -2.3u      +20.7u

A green sanity report is a prerequisite to wiring the bundle into the
daily run — Phase 2c lifts that gate by uploading bundles to R2 only
when they clear a brier-delta threshold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger

from ..config import NRFIConfig, get_default_config
from ..data.storage import NRFIStore
from ..evaluation.metrics import (
    brier_score, log_loss_score, simulated_roi,
)

log = get_logger(__name__)


@dataclass
class SanityRow:
    """One side's metrics on the 2026-to-date game set."""
    label: str
    n_games: int
    base_rate: float
    accuracy: float
    brier: float
    log_loss: float
    roi_units: float = 0.0


@dataclass
class SanityReport:
    ml: SanityRow
    baseline: SanityRow
    accuracy_delta: float = 0.0
    brier_delta: float = 0.0
    log_loss_delta: float = 0.0
    roi_delta: float = 0.0
    passed_min_improvement: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        m, b = self.ml, self.baseline
        lines = [
            "Sanity report — ML bundle vs Poisson baseline",
            "─" * 60,
            f"                       ML bundle    Poisson      Delta",
            f"  n games              {m.n_games:>9}    {b.n_games:>7}",
            f"  base NRFI rate       {m.base_rate:>9.3f}    {b.base_rate:>7.3f}",
            f"  accuracy@.5          {m.accuracy:>9.3f}    {b.accuracy:>7.3f}    {self.accuracy_delta:+7.3f}",
            f"  brier                {m.brier:>9.4f}    {b.brier:>7.4f}    {self.brier_delta:+7.4f}",
            f"  log loss             {m.log_loss:>9.4f}    {b.log_loss:>7.4f}    {self.log_loss_delta:+7.4f}",
            f"  ROI (flat 1u, edge≥4%) {m.roi_units:>+8.2f}u   {b.roi_units:>+6.2f}u   {self.roi_delta:>+7.2f}u",
            "",
            f"  Passed min-improvement gate: {self.passed_min_improvement}",
        ]
        for n in self.notes:
            lines.append(f"  * {n}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def compute_sanity(
    *,
    season: int = 2026,
    config: Optional[NRFIConfig] = None,
    store: Optional[NRFIStore] = None,
    market_provider=None,
    min_brier_improvement: float = 0.005,
    min_log_loss_improvement: float = 0.01,
) -> SanityReport:
    """Score the trained bundle vs the Poisson baseline on `season`.

    Pulls each game's stored feature blob (which contains
    `poisson_p_nrfi` for the deterministic side) plus the ground-truth
    `nrfi` actual, predicts with the loaded ML bundle, and computes
    the headline metrics for each side.
    """
    cfg = (config or get_default_config()).resolve_paths()
    store = store or NRFIStore(cfg.duckdb_path)

    df = store.query_df(
        """
        SELECT g.game_pk, g.game_date, f.feature_blob,
               a.nrfi, a.first_inn_runs
        FROM features f
        JOIN actuals a USING(game_pk)
        JOIN games   g USING(game_pk)
        WHERE g.season = ?
          AND f.model_version = 'elite_nrfi_v1'
        """,
        (int(season),),
    )

    if df is None or df.empty:
        empty = SanityRow(label="empty", n_games=0, base_rate=0.0,
                          accuracy=0.0, brier=0.0, log_loss=0.0)
        return SanityReport(ml=empty, baseline=empty,
                             notes=[f"no rows for season {season}"])

    import numpy as np
    from ..integration.engine_bridge import NRFIEngineBridge

    # ML bundle prediction --------------------------------------------------
    bridge = NRFIEngineBridge.try_load(cfg)
    feature_blobs = [json.loads(b) for b in df["feature_blob"]]
    game_ids = [str(p) for p in df["game_pk"]]

    if bridge.available():
        outputs = bridge.predict_for_features(
            feature_blobs, game_ids=game_ids,
        )
        # The bridge emits NRFI + YRFI — keep only the NRFI side.
        ml_p_by_gid = {
            o.game_id: float(o.fair_prob)
            for o in outputs if o.market_type == "NRFI"
        }
        ml_p = np.array([ml_p_by_gid.get(gid, 0.55) for gid in game_ids])
    else:
        # No bundle on disk — return a degenerate report.
        empty = SanityRow(label="ml-not-loaded", n_games=int(len(df)),
                          base_rate=float(df["nrfi"].astype(int).mean()),
                          accuracy=0.0, brier=0.0, log_loss=0.0)
        baseline_p = np.array([fb.get("poisson_p_nrfi", 0.55) for fb in feature_blobs])
        y = df["nrfi"].astype(int).values
        b_row = SanityRow(
            label="poisson-baseline",
            n_games=int(len(df)),
            base_rate=float(y.mean()),
            accuracy=float(((baseline_p >= 0.5).astype(int) == y).mean()),
            brier=brier_score(baseline_p, y),
            log_loss=log_loss_score(baseline_p, y),
        )
        return SanityReport(ml=empty, baseline=b_row,
                             notes=["ML bundle not loaded; baseline-only report"])

    # Baseline (Poisson) prediction ----------------------------------------
    baseline_p = np.array([fb.get("poisson_p_nrfi", 0.55) for fb in feature_blobs])

    y = df["nrfi"].astype(int).values

    # Optional market-implied — used only for the ROI line.
    market_p = None
    if market_provider is not None:
        market_p = np.array([
            market_provider(int(gpk)) or 0.524
            for gpk in df["game_pk"]
        ])

    ml_row = _row("ml-bundle", ml_p, y, market_p)
    base_row = _row("poisson-baseline", baseline_p, y, market_p)

    report = SanityReport(
        ml=ml_row, baseline=base_row,
        accuracy_delta=ml_row.accuracy - base_row.accuracy,
        brier_delta=ml_row.brier - base_row.brier,
        log_loss_delta=ml_row.log_loss - base_row.log_loss,
        roi_delta=ml_row.roi_units - base_row.roi_units,
    )
    # Improvement gate: lower brier + lower log loss by configurable margins.
    report.passed_min_improvement = (
        report.brier_delta <= -min_brier_improvement and
        report.log_loss_delta <= -min_log_loss_improvement
    )
    if not report.passed_min_improvement:
        report.notes.append(
            f"ML bundle did not clear improvement gate "
            f"(min brier delta -{min_brier_improvement:.4f}, "
            f"min log-loss delta -{min_log_loss_improvement:.4f}). "
            f"Do not promote to production."
        )
    return report


def _row(label, p, y, market_p) -> SanityRow:
    import numpy as np
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    n = len(p)
    if n == 0:
        return SanityRow(label=label, n_games=0, base_rate=0.0,
                          accuracy=0.0, brier=0.0, log_loss=0.0)
    accuracy = float(((p >= 0.5).astype(int) == y).mean())
    brier = brier_score(p, y)
    ll = log_loss_score(p, y)
    roi_units = 0.0
    if market_p is not None:
        roi = simulated_roi(p, y, market_p=market_p, side="auto")
        roi_units = float(roi.units_won)
    return SanityRow(
        label=label, n_games=n,
        base_rate=float(y.mean()),
        accuracy=accuracy, brier=brier, log_loss=ll,
        roi_units=roi_units,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="ML bundle vs Poisson baseline sanity report"
    )
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--min-brier-improvement", type=float, default=0.005)
    parser.add_argument("--min-log-loss-improvement", type=float, default=0.01)
    parser.add_argument(
        "--write-gate-marker", default=None,
        help="When the sanity gate passes, touch a marker file at this "
              "path. CI workflows key off the file's existence to gate "
              "R2 promotion of the trained bundle.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = compute_sanity(
        season=args.season,
        min_brier_improvement=args.min_brier_improvement,
        min_log_loss_improvement=args.min_log_loss_improvement,
    )
    print(report.summary())

    if args.write_gate_marker and report.passed_min_improvement:
        from pathlib import Path as _P
        marker = _P(args.write_gate_marker)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"sanity_gate=passed\n"
            f"brier_delta={report.brier_delta:.6f}\n"
            f"log_loss_delta={report.log_loss_delta:.6f}\n"
            f"accuracy_delta={report.accuracy_delta:.6f}\n"
            f"n_games={report.ml.n_games}\n"
        )
        print(f"  Gate marker written: {marker}")

    return 0 if report.passed_min_improvement else 2


if __name__ == "__main__":
    raise SystemExit(main())
