"""Markdown leaderboard rendering.

Takes a list of :class:`EngineScore`s + the source provenance and
emits a single markdown document: leaderboard table on top, per-engine
reliability bucket table below, source / caveat footer.

Designed to be re-runnable any time --- the output is one document
per ``shootout`` invocation, written under ``parlay_lab/reports/``
and timestamped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .backfill import BackfillSource
from .engines import ENGINES
from .metrics import EngineScore, ParlayOutcome


# Buckets for the reliability table --- predicted joint_prob_corr binned
# vs realized hit rate within each bucket. Same edges the public
# /reliability page uses on singles.
_RELIABILITY_BUCKETS: list[tuple[float, float]] = [
    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
    (0.70, 0.75), (0.75, 0.80), (0.80, 0.85), (0.85, 0.90),
    (0.90, 1.01),  # 1.01 to include 1.0 in the final bucket
]


@dataclass
class ShootoutReport:
    source: BackfillSource
    scores: list[EngineScore]
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_signed(units: float) -> str:
    return f"{units:+.2f}u" if units != 0 else " 0.00u"


def _fmt_pct(p: float) -> str:
    return f"{p:+.2f}%" if p != 0 else " 0.00%"


def _leaderboard_table(scores: list[EngineScore]) -> str:
    # Sort by ROI desc, with hit-rate tiebreak. Calibration shown separately.
    ranked = sorted(
        scores,
        key=lambda s: (s.roi_pct, s.hit_rate, s.n_parlays),
        reverse=True,
    )
    lines = [
        "| # | Engine | Parlays | Active days | W-L-P | Hit rate | ROI | Total P/L | Max drawdown | Avg legs | Brier (joint) |",
        "|---|--------|---------|-------------|-------|----------|-----|-----------|--------------|----------|---------------|",
    ]
    for i, s in enumerate(ranked, 1):
        brier = f"{s.brier_joint:.4f}" if s.brier_joint is not None else "—"
        lines.append(
            f"| {i} | `{s.engine_name}` | {s.n_parlays} | "
            f"{s.n_days_active}/{s.n_days_total} | "
            f"{s.n_wins}-{s.n_losses}-{s.n_pushes} | "
            f"{s.hit_rate*100:.1f}% | {_fmt_pct(s.roi_pct)} | "
            f"{_fmt_signed(s.total_pl_units)} | "
            f"{_fmt_signed(-s.max_drawdown_units)} | "
            f"{s.avg_legs:.2f} | {brier} |"
        )
    return "\n".join(lines)


def _reliability_table(score: EngineScore) -> str:
    """One per-engine reliability table: predicted joint prob vs realized."""
    if not score.outcomes:
        return "_(no graded parlays)_"
    bucket_n: dict[tuple[float, float], int] = {b: 0 for b in _RELIABILITY_BUCKETS}
    bucket_wins: dict[tuple[float, float], int] = {b: 0 for b in _RELIABILITY_BUCKETS}
    for o in score.outcomes:
        if o.result == "PUSH":
            continue
        for b in _RELIABILITY_BUCKETS:
            lo, hi = b
            if lo <= o.joint_prob_corr < hi:
                bucket_n[b] += 1
                if o.result == "WIN":
                    bucket_wins[b] += 1
                break
    lines = [
        "| Predicted | N | Wins | Realized | Δ |",
        "|-----------|---|------|----------|---|",
    ]
    for b in _RELIABILITY_BUCKETS:
        n = bucket_n[b]
        if n == 0:
            continue
        w = bucket_wins[b]
        realized = w / n
        midpoint = (b[0] + min(1.0, b[1])) / 2.0
        delta = realized - midpoint
        lines.append(
            f"| {b[0]*100:4.0f}–{min(1.0, b[1])*100:4.0f}% | "
            f"{n} | {w} | {realized*100:.1f}% | "
            f"{delta*100:+.1f}pp |"
        )
    if len(lines) == 2:
        return "_(no buckets had any graded parlays)_"
    return "\n".join(lines)


def _engine_descriptions() -> str:
    lines = ["| Engine | Description |", "|--------|-------------|"]
    for name, cls in ENGINES.items():
        lines.append(f"| `{name}` | {cls.description} |")
    return "\n".join(lines)


def render(report: ShootoutReport) -> str:
    """Format the entire markdown report --- one self-contained document."""
    src = report.source
    out: list[str] = []
    out.append(f"# Parlay Shootout — {report.generated_at}")
    out.append("")
    out.append(
        f"**Source:** `{src.path.name}` · "
        f"{src.n_rows:,} graded legs across {src.first_date} → {src.last_date}"
    )
    out.append("")
    out.append("## Leaderboard")
    out.append("")
    out.append(_leaderboard_table(report.scores))
    out.append("")
    out.append("Sorted by ROI descending. `Brier (joint)` measures calibration "
                "of the engine's predicted joint probability vs. realized "
                "outcome — lower is better, perfect calibration trends to "
                "≈0.16 for sharp parlays.")
    out.append("")
    out.append("## Engines")
    out.append("")
    out.append(_engine_descriptions())
    out.append("")
    out.append("## Reliability per engine")
    out.append("")
    out.append(
        "Realized hit rate within each predicted-probability bucket. The "
        "`Δ` column is realized minus the bucket midpoint — close to 0 "
        "means the engine's joint-prob estimates match reality."
    )
    out.append("")
    for s in report.scores:
        out.append(f"### `{s.engine_name}`")
        out.append("")
        out.append(_reliability_table(s))
        out.append("")
    out.append("## Caveats")
    out.append("")
    out.append(
        "- Decimal odds are recovered from `units` on WIN rows; "
        "LOSS / PUSH rows fall back to -110 (decimal 1.909). "
        "Comparisons between engines are unbiased; absolute ROI "
        "under-counts +money winners."
    )
    out.append(
        "- Push policy: a leg with `result == 'PUSH'` is dropped from "
        "the parlay; the remaining legs determine W/L."
    )
    out.append(
        "- This is a parlay-CONSTRUCTION shootout. The underlying "
        "singles model isn't re-validated here — every engine sees "
        "the same per-leg probabilities and outcomes."
    )
    return "\n".join(out)


def write_report(report: ShootoutReport, *, out_dir: str | Path) -> Path:
    """Write the rendered report to ``<out_dir>/<timestamp>_shootout.md``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.replace(":", "-")
    path = out / f"{stamp}_shootout.md"
    path.write_text(render(report) + "\n")
    return path
