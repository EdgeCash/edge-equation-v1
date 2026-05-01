"""Walk-forward training pipeline for the NRFI engine (Phase 2b).

Approach
--------

The training corpus (populated by Phase 2a's backfill) contains ~600
days of feature rows + actuals. Naive global cross-validation would
leak time-future information into the model. Walk-forward training
respects causality:

    For each chunk in [start_date, end_date] stepping `chunk_size_days`:
        train_window = [chunk_start - window_months, chunk_start - 1day]
        train  on data inside train_window
        predict on the chunk's days
        append (predicted_p, actual_y, ...) to the calibration set

After the loop:

    final train on [end_date - window_months, end_date]
    fit isotonic calibrator on the walk-forward calibration set
    save TrainedBundle to disk

Default chunk size is 7 days — weekly retraining strikes the right
balance between freshness and runtime (~85 chunks × ~10s/chunk ≈ 14
min vs. ~600 chunks × ~10s/chunk ≈ 100 min for daily). The CLI lets
you override.

Calibration
-----------

The walk-forward calibration set is the *honest* holdout — every
prediction in it was made by a model that didn't see that game. We
fit isotonic on this set (via `RollingHoldoutCalibrator` from the
calibration module), then re-attach the fitted calibrator to the
final TrainedBundle so live predictions inherit the same mapping.

Outputs
-------

* `TrainedBundle` saved to `cfg.model_dir/elite_nrfi_v1_*.pkl`.
* `WalkForwardReport` summarising:
   - dates trained / predicted
   - corpus size at first vs last chunk
   - walk-forward Brier / log-loss / accuracy
   - elapsed seconds
* JSONL of (game_pk, date, predicted_p, actual_y) for downstream
  diagnostics — written to `cfg.cache_dir/walkforward_calibration.jsonl`.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from edge_equation.utils.logging import get_logger

from ..config import NRFIConfig, get_default_config
from ..data.storage import NRFIStore

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ChunkResult:
    """One chunk's training outcome."""
    chunk_start: str
    chunk_end: str
    train_rows: int
    predicted_rows: int
    skipped: bool = False
    error: Optional[str] = None


@dataclass
class WalkForwardReport:
    n_chunks: int = 0
    n_chunks_skipped: int = 0
    n_chunks_failed: int = 0
    train_rows_first: int = 0     # corpus size at the first non-skipped chunk
    train_rows_last: int = 0      # corpus size at the final chunk
    n_predictions: int = 0        # total walk-forward predictions
    walkforward_brier: float = 0.0
    walkforward_log_loss: float = 0.0
    walkforward_accuracy: float = 0.0
    walkforward_base_rate: float = 0.0
    chunks: list[ChunkResult] = field(default_factory=list)
    bundle_saved_to: Optional[str] = None
    calibration_jsonl: Optional[str] = None
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            "Walk-forward training report",
            "─" * 50,
            f"  chunks                 {self.n_chunks}",
            f"  chunks skipped         {self.n_chunks_skipped}",
            f"  chunks failed          {self.n_chunks_failed}",
            f"  train_rows: first      {self.train_rows_first}",
            f"  train_rows: last       {self.train_rows_last}",
            f"  walkforward predictions {self.n_predictions}",
            f"  WF base rate           {self.walkforward_base_rate:.3f}",
            f"  WF accuracy@.5         {self.walkforward_accuracy:.3f}",
            f"  WF brier               {self.walkforward_brier:.4f}",
            f"  WF log loss            {self.walkforward_log_loss:.4f}",
            f"  bundle                 {self.bundle_saved_to or '-'}",
            f"  elapsed                {self.elapsed_seconds:.1f}s",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Corpus loader
# ---------------------------------------------------------------------------

def load_corpus(store: NRFIStore, start_date: str, end_date: str):
    """Pull (features × actuals) rows from DuckDB inside a date range.

    Returns a pandas DataFrame with `game_pk`, `game_date`,
    `feature_blob`, `nrfi`, and `first_inn_runs`.
    """
    return store.query_df(
        """
        SELECT g.game_pk, g.game_date, f.feature_blob,
               a.first_inn_runs, a.nrfi
        FROM features f
        JOIN actuals a USING(game_pk)
        JOIN games   g USING(game_pk)
        WHERE g.game_date BETWEEN ? AND ?
          AND f.model_version = 'elite_nrfi_v1'
        """,
        (start_date, end_date),
    )


# ---------------------------------------------------------------------------
# Walk-forward orchestrator
# ---------------------------------------------------------------------------

def walkforward_train(
    *,
    start_date: str,
    end_date: str,
    window_months: int = 18,
    chunk_size_days: int = 7,
    min_train_rows: int = 200,
    config: Optional[NRFIConfig] = None,
    store: Optional[NRFIStore] = None,
    save_bundle: bool = True,
    calibration_method: str = "isotonic",
    progress_callback=None,
) -> WalkForwardReport:
    """Walk forward through [start_date, end_date] training on each chunk.

    Parameters
    ----------
    start_date : First date the model is allowed to *predict* on.
        The training window for the first chunk extends backward from
        here by `window_months`.
    end_date : Last predicted date (inclusive).
    window_months : Rolling training window. 18 per the project spec.
    chunk_size_days : How far forward to predict before retraining.
        Default 7 (weekly walk-forward).
    min_train_rows : Skip chunks whose training window has fewer than
        this many rows.
    save_bundle : When True, fit a *final* bundle on
        [end_date - window_months, end_date] and persist to disk.
    """
    cfg = (config or get_default_config()).resolve_paths()
    store = store or NRFIStore(cfg.duckdb_path)

    started = time.monotonic()
    report = WalkForwardReport()

    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date)
    if d0 > d1:
        raise ValueError(f"start_date {start_date} > end_date {end_date}")

    # Lazy ML imports — kept here so the slim CI Tests workflow doesn't
    # need [nrfi] extras to import this module's public dataclasses.
    from ..models.model_training import (
        NRFIClassifier, FirstInningRunsRegressor,
        TrainedBundle, MODEL_VERSION,
        expand_feature_blobs, feature_matrix,
    )
    from ..models.poisson_baseline import PoissonGLM
    from ..integration.calibration import CoreIsotonicCalibrator
    from ..models.calibration import Calibrator

    calibration_rows: list[dict] = []
    last_classifier: Optional[NRFIClassifier] = None

    # Chunk loop ------------------------------------------------------------
    chunk_start = d0
    while chunk_start <= d1:
        chunk_end = min(chunk_start + timedelta(days=chunk_size_days - 1), d1)
        train_lo = chunk_start - timedelta(days=int(window_months * 30.5))
        train_hi = chunk_start - timedelta(days=1)

        result = ChunkResult(
            chunk_start=chunk_start.isoformat(),
            chunk_end=chunk_end.isoformat(),
            train_rows=0,
            predicted_rows=0,
        )

        try:
            train_df = load_corpus(store, train_lo.isoformat(),
                                    train_hi.isoformat())
            n_train = 0 if train_df is None else len(train_df)
            result.train_rows = n_train

            if n_train < min_train_rows:
                result.skipped = True
                report.n_chunks_skipped += 1
                if progress_callback:
                    progress_callback(result)
                report.chunks.append(result)
                chunk_start = chunk_end + timedelta(days=1)
                report.n_chunks += 1
                continue

            if report.train_rows_first == 0:
                report.train_rows_first = n_train
            report.train_rows_last = n_train

            wide_train = expand_feature_blobs(train_df)
            X_train, _ = feature_matrix(wide_train)
            y_train_clf = wide_train["nrfi"].astype(int).values
            y_train_reg = wide_train["first_inn_runs"].astype(float).values

            # Fit classifier head only — the regressor is trained once
            # at the end since we only need point-in-time predictions
            # for calibration here, not regressor outputs.
            classifier = NRFIClassifier(blend_with_lgbm=True).fit(
                X_train, y_train_clf,
                params=cfg.model.xgb_classifier_params,
                calibration_holdout_frac=cfg.model.calibration_holdout_frac,
                calibration_method=calibration_method,
                lgbm_params=cfg.model.lgbm_params,
            )
            last_classifier = classifier

            # Predict on the chunk's days.
            pred_df = load_corpus(store, chunk_start.isoformat(),
                                    chunk_end.isoformat())
            if pred_df is not None and not pred_df.empty:
                wide_pred = expand_feature_blobs(pred_df)
                X_pred, _ = feature_matrix(wide_pred)
                # Align columns to classifier's feature set.
                for c in classifier.feature_names:
                    if c not in X_pred.columns:
                        X_pred[c] = 0.0
                X_pred = X_pred[classifier.feature_names]
                p = classifier.predict_proba(X_pred)
                for i, row in wide_pred.reset_index(drop=True).iterrows():
                    calibration_rows.append({
                        "game_pk": int(row["game_pk"]),
                        "game_date": str(row["game_date"]),
                        "predicted_p": float(p[i]),
                        "actual_y": int(row["nrfi"]),
                    })
                result.predicted_rows = int(len(wide_pred))
                report.n_predictions += result.predicted_rows
        except Exception as e:
            log.warning("walk-forward chunk %s..%s failed: %s",
                         result.chunk_start, result.chunk_end, e)
            result.error = str(e)
            report.n_chunks_failed += 1

        if progress_callback:
            progress_callback(result)
        report.chunks.append(result)
        chunk_start = chunk_end + timedelta(days=1)
        report.n_chunks += 1

    # Walk-forward metrics ---------------------------------------------------
    if calibration_rows:
        import numpy as np
        ps = np.asarray([r["predicted_p"] for r in calibration_rows])
        ys = np.asarray([r["actual_y"] for r in calibration_rows], dtype=int)
        report.walkforward_brier = float(np.mean((ps - ys) ** 2))
        eps = 1e-9
        ps_clip = np.clip(ps, eps, 1 - eps)
        report.walkforward_log_loss = float(
            -np.mean(ys * np.log(ps_clip) + (1 - ys) * np.log(1 - ps_clip))
        )
        report.walkforward_accuracy = float(((ps >= 0.5).astype(int) == ys).mean())
        report.walkforward_base_rate = float(ys.mean())

    # Persist the calibration set as JSONL ----------------------------------
    cal_path = Path(cfg.cache_dir) / "walkforward_calibration.jsonl"
    cal_path.parent.mkdir(parents=True, exist_ok=True)
    with cal_path.open("w") as fh:
        for r in calibration_rows:
            fh.write(json.dumps(r) + "\n")
    report.calibration_jsonl = str(cal_path)

    # Final bundle -----------------------------------------------------------
    if save_bundle and calibration_rows:
        try:
            final_lo = d1 - timedelta(days=int(window_months * 30.5))
            final_df = load_corpus(store, final_lo.isoformat(),
                                     d1.isoformat())
            if final_df is None or final_df.empty:
                log.warning("no rows for final training window — skipping bundle save")
            else:
                wide = expand_feature_blobs(final_df)
                X, cols = feature_matrix(wide)
                y_clf = wide["nrfi"].astype(int).values
                y_reg = wide["first_inn_runs"].astype(float).values

                final_clf = NRFIClassifier(blend_with_lgbm=True).fit(
                    X, y_clf,
                    params=cfg.model.xgb_classifier_params,
                    calibration_holdout_frac=cfg.model.calibration_holdout_frac,
                    calibration_method=calibration_method,
                    lgbm_params=cfg.model.lgbm_params,
                )
                # Replace the in-classifier holdout calibrator with one
                # fitted on the FULL walk-forward calibration set —
                # honest, non-leaking.
                cal = Calibrator(method=calibration_method).fit(
                    [r["predicted_p"] for r in calibration_rows],
                    [r["actual_y"] for r in calibration_rows],
                )
                final_clf._calibrator = cal

                final_reg = FirstInningRunsRegressor().fit(
                    X, y_reg, params=cfg.model.xgb_poisson_params,
                )
                glm = PoissonGLM().fit(X.values, y_reg, feature_names=cols)
                bundle = TrainedBundle(
                    classifier=final_clf, regressor=final_reg,
                    poisson_glm=glm, feature_names=cols,
                    model_version=MODEL_VERSION,
                )
                bundle.save(cfg.model_dir)
                report.bundle_saved_to = str(cfg.model_dir)
                log.info("saved final bundle to %s", cfg.model_dir)
        except Exception as e:
            log.exception("final bundle training failed: %s", e)

    report.elapsed_seconds = time.monotonic() - started
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Iterable[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="NRFI walk-forward training pipeline (Phase 2b)"
    )
    parser.add_argument(
        "--from", dest="start_date", default="2025-04-01",
        help="First date to predict on (default: 2025-04-01 — gives ~7 months "
              "of warm-up before predicting starts).",
    )
    parser.add_argument(
        "--to", dest="end_date", default=date.today().isoformat(),
    )
    parser.add_argument("--window-months", type=int, default=18)
    parser.add_argument("--chunk-days", type=int, default=7)
    parser.add_argument("--min-train-rows", type=int, default=200)
    parser.add_argument("--no-save-bundle", action="store_true")
    parser.add_argument("--calibration-method",
                          choices=("isotonic", "platt", "beta"),
                          default="isotonic")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    def _on_progress(c: ChunkResult) -> None:
        if args.quiet:
            return
        if c.skipped:
            tag = "SKIP"
            extra = f"  train_rows={c.train_rows} (< min)"
        elif c.error:
            tag = "FAIL"
            extra = f"  err={c.error}"
        else:
            tag = "OK  "
            extra = f"  train_rows={c.train_rows}  predicted={c.predicted_rows}"
        print(f"  {tag}  {c.chunk_start}..{c.chunk_end}{extra}")

    report = walkforward_train(
        start_date=args.start_date,
        end_date=args.end_date,
        window_months=args.window_months,
        chunk_size_days=args.chunk_days,
        min_train_rows=args.min_train_rows,
        save_bundle=not args.no_save_bundle,
        calibration_method=args.calibration_method,
        progress_callback=_on_progress,
    )
    print()
    print(report.summary())
    return 0 if report.n_chunks_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
