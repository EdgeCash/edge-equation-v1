"""Calibration-drift alert checker.

Walks every sport's CLV-tracker picks log, computes the current
rolling Brier score + ROI, classifies each sport as ``ok`` /
``warning`` / ``critical`` / ``no_data``, and writes one JSON file
the website renders as a top-of-page banner.

Why: long-run profitability tracks calibration. The publish gate
is Brier < 0.246; if any sport's Brier drifts above that, the
operator (and the visitor) deserves an honest warning instead of a
silent demotion.

Output contract — read by `web/lib/alerts.ts`:

    public/data/calibration_alerts.json
      {
        "version": 1,
        "generated_at": "2026-05-06T19:32:00Z",
        "publish_gate": 0.246,
        "summary": {
          "mlb":   {"brier": 0.234, "n": 432, "roi_pct": 2.4, "status": "ok"},
          "wnba":  {"brier": 0.251, "n": 187, "roi_pct": -1.1, "status": "warning"},
          "nfl":   {"brier": null,  "n": 0,   "roi_pct": null, "status": "no_data"},
          "ncaaf": {"brier": null,  "n": 0,   "roi_pct": null, "status": "no_data"}
        },
        "alerts": [
          {
            "sport": "wnba", "level": "warning", "metric": "brier",
            "value": 0.251, "threshold": 0.246, "n_picks": 187,
            "message": "WNBA Brier 0.251 — above publish gate over
                         187 graded picks. Calibration drifting."
          }
        ]
      }

The `summary` block is unconditional — the website uses it for
the per-sport reliability badge. The `alerts` array is non-empty
only when at least one sport breaches a threshold.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Thresholds — single source of truth.
# ---------------------------------------------------------------------------


PUBLISH_GATE_BRIER: float = 0.246          # below = production-quality
WARNING_BRIER:     float = 0.246           # above = warning
CRITICAL_BRIER:    float = 0.260           # above = critical drift

# ROI thresholds applied over the last `ROI_LOOKBACK` graded picks.
WARNING_ROI_PCT:   float = 0.0             # negative ROI
CRITICAL_ROI_PCT:  float = -3.0            # sustained loss

# Minimum graded sample size before an alert fires. Below this the
# Brier scalar is too noisy; we report `no_data` and stay silent.
MIN_GRADED_SAMPLE: int   = 30


SPORT_LABELS: dict[str, str] = {
    "mlb":   "MLB",
    "wnba":  "WNBA",
    "nfl":   "NFL",
    "ncaaf": "NCAAF",
}


# ---------------------------------------------------------------------------
# Output dataclasses.
# ---------------------------------------------------------------------------


@dataclass
class SportSummary:
    sport: str
    brier: Optional[float] = None
    roi_pct: Optional[float] = None
    n: int = 0
    status: str = "no_data"     # ok | warning | critical | no_data


@dataclass
class Alert:
    sport: str
    level: str                  # warning | critical
    metric: str                 # brier | roi
    value: float
    threshold: float
    n_picks: int
    message: str


@dataclass
class AlertReport:
    version: int = 1
    generated_at: str = ""
    publish_gate: float = PUBLISH_GATE_BRIER
    summary: dict[str, dict] = field(default_factory=dict)
    alerts: List[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-sport pick loader (mirrors lib/picks-history.ts).
# ---------------------------------------------------------------------------


@dataclass
class PickRow:
    sport: str
    model_prob: Optional[float]
    result: Optional[str]       # WIN | LOSS | PUSH | None
    units: Optional[float]


def load_picks_for_sport(
    sport: str, *, data_root: Path,
) -> list[PickRow]:
    """Load and lightly-normalise the per-sport CLV picks log."""
    file = data_root / sport / "picks_log.json"
    if not file.exists():
        return []
    try:
        parsed = json.loads(file.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning(
            "calibration_alerts: %s/picks_log.json unreadable (%s): %s",
            sport, type(e).__name__, e,
        )
        return []
    rows: list[PickRow] = []
    for raw in parsed.get("picks") or []:
        rows.append(PickRow(
            sport=sport,
            model_prob=_num_or_none(raw.get("model_prob")),
            result=_result_or_none(raw.get("result")),
            units=_num_or_none(raw.get("units")),
        ))
    return rows


def _num_or_none(v) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _result_or_none(v) -> Optional[str]:
    if v in ("WIN", "LOSS", "PUSH"):
        return v
    return None


# ---------------------------------------------------------------------------
# Scoring helpers.
# ---------------------------------------------------------------------------


def brier_score(rows: Sequence[PickRow]) -> tuple[Optional[float], int]:
    """Return ``(brier, n_graded)``. ``brier`` is None when
    n_graded is 0 — a missing scalar is more honest than 0.000."""
    graded = [
        r for r in rows
        if (r.result == "WIN" or r.result == "LOSS")
        and r.model_prob is not None
    ]
    if not graded:
        return None, 0
    sse = 0.0
    for r in graded:
        outcome = 1.0 if r.result == "WIN" else 0.0
        diff = (r.model_prob or 0.0) - outcome
        sse += diff * diff
    return round(sse / len(graded), 4), len(graded)


def roi_pct(rows: Sequence[PickRow], *, lookback: int = 200) -> tuple[Optional[float], int]:
    """Return ``(roi_pct, n_graded)`` over the last ``lookback`` graded picks.

    `units` is the realised P/L in flat-stake units. ROI = sum(units)
    / n_graded. None when no graded picks.
    """
    graded = [
        r for r in rows
        if r.result in ("WIN", "LOSS") and r.units is not None
    ]
    sample = graded[-lookback:]
    if not sample:
        return None, 0
    total = sum((r.units or 0.0) for r in sample)
    return round((total / len(sample)) * 100.0, 2), len(sample)


# ---------------------------------------------------------------------------
# Classifier + alert builder.
# ---------------------------------------------------------------------------


def classify_sport(summary: SportSummary) -> str:
    if summary.n < MIN_GRADED_SAMPLE:
        return "no_data"
    if summary.brier is None:
        return "no_data"
    if summary.brier >= CRITICAL_BRIER:
        return "critical"
    if summary.brier >= WARNING_BRIER:
        return "warning"
    if summary.roi_pct is not None and summary.roi_pct <= CRITICAL_ROI_PCT:
        return "critical"
    if summary.roi_pct is not None and summary.roi_pct < WARNING_ROI_PCT:
        return "warning"
    return "ok"


def build_alerts(summaries: Iterable[SportSummary]) -> list[Alert]:
    out: list[Alert] = []
    for s in summaries:
        if s.status not in ("warning", "critical"):
            continue
        sport_label = SPORT_LABELS.get(s.sport, s.sport.upper())
        # Brier alert (preferred — primary signal)
        if s.brier is not None and s.brier >= WARNING_BRIER:
            level = "critical" if s.brier >= CRITICAL_BRIER else "warning"
            out.append(Alert(
                sport=s.sport,
                level=level,
                metric="brier",
                value=float(s.brier),
                threshold=PUBLISH_GATE_BRIER,
                n_picks=s.n,
                message=(
                    f"{sport_label} Brier {s.brier:.3f} — above publish "
                    f"gate {PUBLISH_GATE_BRIER:.3f} over {s.n} graded "
                    f"picks. Calibration drifting; markets may be "
                    f"demoted until it recovers."
                ),
            ))
        # ROI alert — fires alongside Brier when ROI is also negative.
        if s.roi_pct is not None and s.roi_pct < WARNING_ROI_PCT:
            level = (
                "critical" if s.roi_pct <= CRITICAL_ROI_PCT
                else "warning"
            )
            out.append(Alert(
                sport=s.sport,
                level=level,
                metric="roi",
                value=float(s.roi_pct),
                threshold=WARNING_ROI_PCT,
                n_picks=s.n,
                message=(
                    f"{sport_label} ROI {s.roi_pct:+.1f}% — below "
                    f"break-even over the last {s.n} graded picks. "
                    f"Engine is bleeding edge."
                ),
            ))
    return out


# ---------------------------------------------------------------------------
# Top-level builder.
# ---------------------------------------------------------------------------


def build_report(
    *,
    sports: Iterable[str] = ("mlb", "wnba", "nfl", "ncaaf"),
    data_root: Optional[Path] = None,
) -> AlertReport:
    """Build the calibration-alert report. Reads from each sport's
    `picks_log.json` under `data_root` (defaults to repo
    `website/public/data/`)."""
    base = data_root or _default_data_root()
    summaries: list[SportSummary] = []
    for sport in sports:
        rows = load_picks_for_sport(sport, data_root=base)
        brier, n_brier = brier_score(rows)
        roi, _ = roi_pct(rows)
        s = SportSummary(
            sport=sport, brier=brier, roi_pct=roi, n=n_brier,
        )
        s.status = classify_sport(s)
        summaries.append(s)
    alerts = build_alerts(summaries)
    return AlertReport(
        generated_at=_now_iso(),
        summary={
            s.sport: {
                "brier": s.brier,
                "n": s.n,
                "roi_pct": s.roi_pct,
                "status": s.status,
            }
            for s in summaries
        },
        alerts=[asdict(a) for a in alerts],
    )


def write_report(report: AlertReport, *, out_path: Optional[Path] = None) -> Path:
    """Persist the report. Returns the written path."""
    target = out_path or _default_output_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": report.version,
        "generated_at": report.generated_at,
        "publish_gate": report.publish_gate,
        "summary": report.summary,
        "alerts": report.alerts,
    }
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return target


def log_alerts_to_console(report: AlertReport) -> None:
    """Print warnings to stdout (consumed by the GitHub Actions log)."""
    if not report.alerts:
        print("[calibration-alerts] No drift alerts — every sport "
              "calibrated within publish gate.")
        return
    print(
        f"[calibration-alerts] {len(report.alerts)} alert(s) — "
        f"engine output may be unreliable until calibration recovers:",
    )
    for a in report.alerts:
        # GitHub Actions ::warning so the alert surfaces in the run
        # summary (lights up red in the UI).
        prefix = "::warning" if a["level"] == "warning" else "::error"
        print(f"{prefix}::{a['sport']} {a['metric']} drift — {a['message']}")


# ---------------------------------------------------------------------------
# CLI hook.
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Compute calibration-drift alerts across sports.",
    )
    parser.add_argument(
        "--sports", default="mlb,wnba,nfl,ncaaf",
        help="Comma-separated sport keys to check.",
    )
    parser.add_argument(
        "--data-root", default=None,
        help="Override the website data root (default: website/public/data).",
    )
    parser.add_argument(
        "--out-path", default=None,
        help="Override the output path (default: "
              "website/public/data/calibration_alerts.json).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Skip the console alert-log emission.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    sports = [s.strip().lower() for s in args.sports.split(",") if s.strip()]
    data_root = Path(args.data_root) if args.data_root else None
    out_path = Path(args.out_path) if args.out_path else None
    report = build_report(sports=sports, data_root=data_root)
    written = write_report(report, out_path=out_path)
    if not args.quiet:
        log_alerts_to_console(report)
    print(f"[calibration-alerts] wrote → {written}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _default_data_root() -> Path:
    return _repo_root() / "website" / "public" / "data"


def _default_output_path() -> Path:
    return _default_data_root() / "calibration_alerts.json"


def _repo_root() -> Path:
    """Best-effort repo-root resolver."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "pyproject.toml").exists():
            return ancestor
    return Path.cwd()


if __name__ == "__main__":
    import sys
    sys.exit(main())
