"""Calibration audit — empirically compare calibrator alternatives.

Read-only diagnostic. Takes a vector of (raw_score, y_true) pairs and
runs them through every calibrator in
``models.calibration_alternatives.ALTERNATIVE_NAMES`` using a single
held-out split (or cross-validation), then emits a side-by-side report
of:

* sample size (fit / eval)
* Brier score
* log-loss
* expected calibration error (ECE)
* output std (signal preservation)
* min / max / mean of the calibrated output
* counts >= 55% / 58% / 64% / 70% (the NRFI tier ladder)

The audit deliberately does not pick a winner — operators inspect the
table and decide. Multiple "winners" (lowest Brier vs highest ≥64%
count) are tradeoffs worth seeing rather than collapsing.

Two ways to feed it data:

1. **Trained-bundle path** — pulls the bundle's walk-forward holdout
   predictions from DuckDB if they're persisted, runs each calibrator
   on a hold-out split. Use when you want to audit live model output.
2. **Synthetic / arbitrary vectors** — pass `raw_scores` and `y_true`
   directly. Use for unit tests or for auditing an externally-saved
   prediction vector (e.g. an exported CSV).

CLI
~~~

::

    # Synthetic / vector input
    python -m edge_equation.engines.nrfi.evaluation.calibration_audit \\
        --csv /tmp/holdout.csv

    # Pull from the trained bundle's recorded walk-forward predictions
    python -m edge_equation.engines.nrfi.evaluation.calibration_audit \\
        --from-bundle
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np

from ..models.calibration_alternatives import (
    ALTERNATIVE_NAMES, build_calibrator,
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibratorResult:
    """One calibrator's eval-set summary."""
    name: str
    n_fit: int
    n_eval: int
    brier: float
    log_loss: float
    ece: float
    out_min: float
    out_max: float
    out_mean: float
    out_std: float
    ge_55: int
    ge_58: int
    ge_64: int
    ge_70: int

    def line(self) -> str:
        return (
            f"  {self.name:<20} n={self.n_eval:<4} "
            f"brier={self.brier:.4f} ll={self.log_loss:.4f} "
            f"ece={self.ece:.4f} std={self.out_std * 100:5.2f}% "
            f"min={self.out_min * 100:5.1f}% max={self.out_max * 100:5.1f}% "
            f">=55/{self.ge_55:<3} >=58/{self.ge_58:<3} "
            f">=64/{self.ge_64:<3} >=70/{self.ge_70:<3}"
        )


@dataclass
class AuditReport:
    """Roll-up of one audit run."""
    raw_summary: CalibratorResult
    calibrator_results: list[CalibratorResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "Calibration audit",
            "=" * 96,
            "",
            "Raw model (no calibration applied — reference point):",
            self.raw_summary.line(),
            "",
            "Calibrator alternatives (fit on train split, evaluated on holdout):",
        ]
        # Sort by log-loss ascending for readability — best-fitting first.
        sorted_results = sorted(self.calibrator_results, key=lambda r: r.log_loss)
        lines.extend(r.line() for r in sorted_results)
        if self.notes:
            lines.extend(["", "Notes:"])
            for n in self.notes:
                lines.append(f"  * {n}")
        lines.extend([
            "",
            "Reading the table:",
            "  brier      lower is better; base-rate predictor scores ~0.250 on a 50/50 market",
            "  ll         log-loss; lower is better",
            "  ece        expected calibration error; lower is better",
            "  std        output dispersion; collapse to ~1% means calibrator is over-flattening",
            "  >=64       count of slate-day-equivalent picks landing at STRONG threshold",
            "",
            "If no calibrator beats raw on Brier AND lifts >=64 above 0, the ceiling is",
            "feature signal (not calibration). See CALIBRATION_AUDIT_INTERPRETATION.md.",
        ])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core audit
# ---------------------------------------------------------------------------


def run_audit(
    raw_scores: Sequence[float],
    y_true: Sequence[int],
    *,
    train_frac: float = 0.7,
    seed: int = 42,
    calibrator_names: Sequence[str] = ALTERNATIVE_NAMES,
    factory: Callable[[str], object] = build_calibrator,
) -> AuditReport:
    """Run every calibrator over a single train/eval split.

    Parameters
    ----------
    raw_scores : raw classifier outputs in [0, 1]. Typically the
        bundle's walk-forward holdout predictions.
    y_true : ground-truth NRFI/YRFI binary labels.
    train_frac : fraction of the data used to fit each calibrator;
        the remainder is the eval set every calibrator is scored on.
        0.7 leaves ~750 eval samples out of a 2,500-sample corpus —
        enough that small ECE differences are signal not noise.
    seed : RNG seed for the train/eval split.
    """
    raw = np.asarray(raw_scores, dtype=float).reshape(-1)
    y = np.asarray(y_true, dtype=int).reshape(-1)
    if raw.size != y.size:
        raise ValueError(
            f"raw_scores and y_true must align: {raw.size} vs {y.size}"
        )
    if raw.size < 10:
        raise ValueError(
            f"audit needs at least 10 samples, got {raw.size}"
        )

    rng = np.random.default_rng(seed)
    perm = rng.permutation(raw.size)
    n_train = max(1, int(round(raw.size * train_frac)))
    train_idx = perm[:n_train]
    eval_idx = perm[n_train:]
    if eval_idx.size == 0:
        # Tiny corpus — fall back to fit-and-eval-on-same set with a note.
        eval_idx = train_idx

    raw_train, raw_eval = raw[train_idx], raw[eval_idx]
    y_train, y_eval = y[train_idx], y[eval_idx]

    raw_summary = _summarize("raw", raw_eval, raw_eval, y_eval)
    calibrator_results: list[CalibratorResult] = []
    notes: list[str] = []
    for name in calibrator_names:
        try:
            cal = factory(name)
            cal.fit(raw_train, y_train)
            p_eval = cal.transform(raw_eval)
            calibrator_results.append(
                _summarize(name, raw_eval, p_eval, y_eval, n_fit=raw_train.size),
            )
        except Exception as e:
            notes.append(f"{name} failed: {e}")
    if eval_idx is train_idx:
        notes.append("eval set was empty after split — scores are train-set only")
    return AuditReport(
        raw_summary=raw_summary,
        calibrator_results=calibrator_results,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _summarize(
    name: str,
    raw_eval: np.ndarray,
    p_eval: np.ndarray,
    y_eval: np.ndarray,
    *,
    n_fit: Optional[int] = None,
) -> CalibratorResult:
    p = np.clip(np.asarray(p_eval, dtype=float).reshape(-1), 1e-9, 1 - 1e-9)
    y = np.asarray(y_eval, dtype=int).reshape(-1)
    brier = float(np.mean((p - y) ** 2))
    ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    ece = _expected_calibration_error(p, y, n_bins=10)
    return CalibratorResult(
        name=name,
        n_fit=int(n_fit) if n_fit is not None else int(raw_eval.size),
        n_eval=int(p.size),
        brier=brier,
        log_loss=ll,
        ece=ece,
        out_min=float(p.min()),
        out_max=float(p.max()),
        out_mean=float(p.mean()),
        out_std=float(p.std()),
        ge_55=int((p >= 0.55).sum()),
        ge_58=int((p >= 0.58).sum()),
        ge_64=int((p >= 0.64).sum()),
        ge_70=int((p >= 0.70).sum()),
    )


def _expected_calibration_error(
    p: np.ndarray, y: np.ndarray, *, n_bins: int = 10,
) -> float:
    """ECE — average over bins of |bin-mean-prob − bin-empirical-rate|,
    weighted by bin size. Lower = better calibrated."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    n = p.size
    if n == 0:
        return 0.0
    ece = 0.0
    for b in range(n_bins):
        mask = idx == b
        if mask.any():
            bin_n = int(mask.sum())
            bin_p = float(p[mask].mean())
            bin_y = float(y[mask].mean())
            ece += (bin_n / n) * abs(bin_p - bin_y)
    return float(ece)


# ---------------------------------------------------------------------------
# Bundle-loading helper (optional path)
# ---------------------------------------------------------------------------


def load_bundle_holdout_predictions():
    """Pull walk-forward holdout (raw_score, y_true) pairs.

    Returns ``(raw_scores, y_true)`` as numpy arrays. Two paths in
    priority order:

    1. **Bundle attributes.** If ``TrainedBundle`` was extended to carry
       ``walkforward_raw_scores`` + ``walkforward_y_true``, use them
       directly.
    2. **walkforward_calibration.jsonl.** Default in-tree path — the
       walk-forward trainer writes this JSONL to ``cfg.cache_dir`` on
       every run. Each row carries ``predicted_p`` + ``actual_y``;
       ``predicted_p`` is the *raw* model score (no calibration applied
       at the chunk-prediction step), so it's exactly what the audit
       wants as input.

    Returns ``None`` if neither source is available — operator can run
    ``python -m edge_equation.engines.nrfi.training.walkforward`` to
    populate the JSONL.
    """
    import json
    from pathlib import Path

    from ..config import get_default_config

    cfg = get_default_config().resolve_paths()

    # Path 1: bundle attributes (only present if a future trainer
    # writes them onto the pickle).
    try:
        from ..models.model_training import MODEL_VERSION, TrainedBundle
        bundle = TrainedBundle.load(cfg.model_dir, MODEL_VERSION)
        raw = getattr(bundle, "walkforward_raw_scores", None)
        y = getattr(bundle, "walkforward_y_true", None)
        if raw is not None and y is not None:
            return np.asarray(raw, dtype=float), np.asarray(y, dtype=int)
    except Exception:
        pass

    # Path 2: walkforward_calibration.jsonl
    jsonl_path = Path(cfg.cache_dir) / "walkforward_calibration.jsonl"
    if not jsonl_path.exists():
        return None
    raw_list: list[float] = []
    y_list: list[int] = []
    with jsonl_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                raw_list.append(float(row["predicted_p"]))
                y_list.append(int(row["actual_y"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    if not raw_list:
        return None
    return np.asarray(raw_list, dtype=float), np.asarray(y_list, dtype=int)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare NRFI calibrator alternatives on holdout predictions."
    )
    parser.add_argument(
        "--csv",
        help="CSV with columns 'raw' and 'y'. Used when --from-bundle is unavailable.",
    )
    parser.add_argument(
        "--from-bundle",
        action="store_true",
        help="Pull raw scores + labels from the trained bundle's walk-forward record.",
    )
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.from_bundle:
        loaded = load_bundle_holdout_predictions()
        if loaded is None:
            print(
                "No walk-forward holdout record on the bundle. "
                "Re-run nrfi.training.walkforward to persist one.",
                file=sys.stderr,
            )
            return 2
        raw, y = loaded
    elif args.csv:
        import csv
        raw_list: list[float] = []
        y_list: list[int] = []
        with open(args.csv, newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                raw_list.append(float(row["raw"]))
                y_list.append(int(row["y"]))
        raw = np.asarray(raw_list, dtype=float)
        y = np.asarray(y_list, dtype=int)
    else:
        print(
            "Provide --from-bundle or --csv <path>. See module docstring.",
            file=sys.stderr,
        )
        return 2

    report = run_audit(
        raw, y, train_frac=args.train_frac, seed=args.seed,
    )
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
