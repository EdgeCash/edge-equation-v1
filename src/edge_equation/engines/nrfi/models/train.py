"""Production weekly training entry point for the NRFI engine.

This module is intentionally a thin orchestration layer over the mature
walk-forward trainer in ``nrfi.training.walkforward``.  The production policy is
fixed here:

* rolling 18-month training windows;
* weekly retraining chunks;
* isotonic calibration by default;
* 2025+2026 walk-forward reliability reporting;
* a persisted JSON manifest next to the model bundle so deployments can audit
  exactly which window produced the active bundle.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from edge_equation.utils.logging import get_logger

from ..calibration import BinSummary, reliability_summary
from ..config import NRFIConfig, get_default_config
from ..data.storage import NRFIStore
from ..evaluation.metrics import brier_score, log_loss_score
from ..training.walkforward import WalkForwardReport, walkforward_train

log = get_logger(__name__)


DEFAULT_START_DATE = "2025-01-01"
DEFAULT_WINDOW_MONTHS = 18
DEFAULT_CHUNK_DAYS = 7
DEFAULT_MIN_TRAIN_ROWS = 200
DEFAULT_CALIBRATION_METHOD = "isotonic"
MANIFEST_NAME = "elite_nrfi_v1_training_manifest.json"


@dataclass(frozen=True)
class CorpusBounds:
    """Available training corpus bounds discovered from DuckDB."""

    min_date: str
    max_date: str
    train_rows: int
    start_date: str


@dataclass(frozen=True)
class ReliabilitySlice:
    """Backtest/reliability metrics for one calendar-year slice."""

    label: str
    n: int
    brier: float
    log_loss: float
    base_rate: float
    bins: list[BinSummary] = field(default_factory=list)

    def line(self) -> str:
        if self.n == 0:
            return f"  {self.label:<8} no walk-forward predictions"
        return (
            f"  {self.label:<8} n={self.n:<4} "
            f"brier={self.brier:.4f} logloss={self.log_loss:.4f} "
            f"base={self.base_rate:.3f}"
        )


@dataclass
class ProductionTrainingReport:
    """Operator-facing report for a production training run."""

    start_date: str
    end_date: str
    window_months: int
    chunk_days: int
    calibration_method: str
    walkforward: WalkForwardReport
    reliability: list[ReliabilitySlice] = field(default_factory=list)
    manifest_path: Optional[str] = None

    def summary(self) -> str:
        lines = [
            "NRFI production training report",
            "-" * 56,
            f"  train policy           rolling {self.window_months} months",
            f"  retrain cadence        {self.chunk_days}-day chunks",
            f"  calibration            {self.calibration_method}",
            f"  predict window         {self.start_date}..{self.end_date}",
            "",
            self.walkforward.summary(),
            "",
            "Reliability slices",
        ]
        lines.extend(r.line() for r in self.reliability)
        if self.manifest_path:
            lines.extend(["", f"Manifest: {self.manifest_path}"])
        return "\n".join(lines)


def train_production_model(
    *,
    start_date: str = DEFAULT_START_DATE,
    end_date: Optional[str] = None,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    min_train_rows: int = DEFAULT_MIN_TRAIN_ROWS,
    calibration_method: str = DEFAULT_CALIBRATION_METHOD,
    config: Optional[NRFIConfig] = None,
    save_bundle: bool = True,
    quiet: bool = False,
) -> ProductionTrainingReport:
    """Run the production weekly rolling trainer and save an audit manifest."""

    cfg = (config or get_default_config()).resolve_paths()
    end = end_date or date.today().isoformat()

    def _progress(chunk) -> None:
        if quiet:
            return
        if chunk.skipped:
            tag = "SKIP"
            detail = f"train_rows={chunk.train_rows}"
        elif chunk.error:
            tag = "FAIL"
            detail = chunk.error
        else:
            tag = "OK"
            detail = f"train_rows={chunk.train_rows} predicted={chunk.predicted_rows}"
        print(f"  {tag:<4} {chunk.chunk_start}..{chunk.chunk_end} {detail}")

    wf = walkforward_train(
        start_date=start_date,
        end_date=end,
        window_months=window_months,
        chunk_size_days=chunk_days,
        min_train_rows=min_train_rows,
        config=cfg,
        save_bundle=save_bundle,
        calibration_method=calibration_method,
        progress_callback=_progress,
    )
    reliability = _reliability_from_jsonl(wf.calibration_jsonl)
    report = ProductionTrainingReport(
        start_date=start_date,
        end_date=end,
        window_months=window_months,
        chunk_days=chunk_days,
        calibration_method=calibration_method,
        walkforward=wf,
        reliability=reliability,
    )
    report.manifest_path = str(_write_manifest(cfg, report))
    return report


def train_full_available_corpus(
    *,
    min_train_rows: int = DEFAULT_MIN_TRAIN_ROWS,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    calibration_method: str = DEFAULT_CALIBRATION_METHOD,
    config: Optional[NRFIConfig] = None,
    save_bundle: bool = True,
    quiet: bool = False,
) -> ProductionTrainingReport:
    """Train on every feature/actual row currently available in DuckDB.

    The first walk-forward prediction date is chosen as the earliest date with
    at least ``min_train_rows`` prior trainable rows. This prevents the common
    "small slice" mistake where early chunks all skip or calibrate on tiny
    samples even though the database contains a larger corpus.
    """

    cfg = (config or get_default_config()).resolve_paths()
    store = NRFIStore(cfg.duckdb_path)
    df = store.query_df(
        """
        SELECT g.game_date, COUNT(*) AS n
        FROM features f
        JOIN actuals a USING(game_pk)
        JOIN games g USING(game_pk)
        WHERE g.game_date BETWEEN '2025-01-01' AND '2026-12-31'
        GROUP BY g.game_date
        ORDER BY g.game_date
        """
    )
    if df is None or df.empty:
        raise RuntimeError("No trainable feature/actual rows found in DuckDB")

    cumulative = 0
    start_date = None
    for _, row in df.iterrows():
        if cumulative >= min_train_rows:
            start_date = str(row.game_date)[:10]
            break
        cumulative += int(row.n)
    if start_date is None:
        raise RuntimeError(
            f"Only {cumulative} trainable rows available; need at least "
            f"{min_train_rows} before first walk-forward chunk"
        )
    end_date = str(df.iloc[-1].game_date)[:10]
    if not quiet:
        total = int(df["n"].sum())
        print(
            f"Full corpus: {total} trainable games, "
            f"date window {str(df.iloc[0].game_date)[:10]}..{end_date}, "
            f"walk-forward starts {start_date}"
        )

    return train_production_model(
        start_date=start_date,
        end_date=end_date,
        window_months=window_months,
        chunk_days=chunk_days,
        min_train_rows=min_train_rows,
        calibration_method=calibration_method,
        config=cfg,
        save_bundle=save_bundle,
        quiet=quiet,
    )


def _reliability_from_jsonl(path: Optional[str]) -> list[ReliabilitySlice]:
    """Build 2025/2026 reliability slices from walk-forward predictions."""

    if not path or not Path(path).exists():
        return [
            ReliabilitySlice("2025", 0, 0.0, 0.0, 0.0, []),
            ReliabilitySlice("2026", 0, 0.0, 0.0, 0.0, []),
        ]

    rows: list[dict] = []
    with Path(path).open() as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))

    out: list[ReliabilitySlice] = []
    for year in ("2025", "2026"):
        slice_rows = [r for r in rows if str(r.get("game_date", "")).startswith(year)]
        if not slice_rows:
            out.append(ReliabilitySlice(year, 0, 0.0, 0.0, 0.0, []))
            continue
        probs = [float(r["predicted_p"]) for r in slice_rows]
        actuals = [int(r["actual_y"]) for r in slice_rows]
        out.append(ReliabilitySlice(
            label=year,
            n=len(slice_rows),
            brier=brier_score(probs, actuals),
            log_loss=log_loss_score(probs, actuals),
            base_rate=sum(actuals) / max(1, len(actuals)),
            bins=reliability_summary(probs, actuals, n_bins=10),
        ))
    return out


def _write_manifest(cfg: NRFIConfig, report: ProductionTrainingReport) -> Path:
    """Persist a small deployment/audit manifest next to model artifacts."""

    path = Path(cfg.model_dir) / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "start_date": report.start_date,
        "end_date": report.end_date,
        "window_months": report.window_months,
        "chunk_days": report.chunk_days,
        "calibration_method": report.calibration_method,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact_version": _artifact_version(report),
        "walkforward": asdict(report.walkforward),
        "reliability": [asdict(r) for r in report.reliability],
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def _artifact_version(report: ProductionTrainingReport) -> str:
    """Human-readable model artifact version for dashboards/runbooks."""

    compact_end = report.end_date.replace("-", "")
    return f"elite_nrfi_v1_{compact_end}_wf{report.walkforward.n_predictions}"


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train the production NRFI model with rolling calibration."
    )
    parser.add_argument("--from", dest="start_date", default=DEFAULT_START_DATE)
    parser.add_argument("--to", dest="end_date", default=date.today().isoformat())
    parser.add_argument("--window-months", type=int, default=DEFAULT_WINDOW_MONTHS)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--min-train-rows", type=int, default=DEFAULT_MIN_TRAIN_ROWS)
    parser.add_argument(
        "--calibration-method",
        choices=("isotonic", "platt"),
        default=DEFAULT_CALIBRATION_METHOD,
    )
    parser.add_argument(
        "--full-corpus",
        action="store_true",
        help="Discover and train on the full feature/actual corpus in DuckDB.",
    )
    parser.add_argument("--no-save-bundle", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    train_fn = train_full_available_corpus if args.full_corpus else train_production_model
    kwargs = {
        "window_months": args.window_months,
        "chunk_days": args.chunk_days,
        "min_train_rows": args.min_train_rows,
        "calibration_method": args.calibration_method,
        "save_bundle": not args.no_save_bundle,
        "quiet": args.quiet,
    }
    if not args.full_corpus:
        kwargs["start_date"] = args.start_date
        kwargs["end_date"] = args.end_date
    report = train_fn(**kwargs)
    print(report.summary())
    return 0 if report.walkforward.n_chunks_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
