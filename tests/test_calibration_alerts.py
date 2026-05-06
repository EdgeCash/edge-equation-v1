"""Tests for the calibration-drift alert checker.

Covers the contract every consumer depends on:

  - Brier above the publish gate fires a warning; further drift
    fires a critical alert.
  - Negative ROI over the rolling lookback fires alongside Brier
    when both apply.
  - Below the minimum graded sample, status is `no_data` and no
    alert fires (small samples are too noisy to declare drift).
  - The output JSON shape matches what `web/lib/alerts.ts` reads
    (version, generated_at, summary, alerts, publish_gate).
  - Tolerant loaders: missing picks_log returns empty, malformed
    JSON returns empty, gracefully.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from edge_equation.exporters.calibration_alerts import (
    PUBLISH_GATE_BRIER,
    AlertReport,
    PickRow,
    brier_score,
    build_alerts,
    build_report,
    classify_sport,
    load_picks_for_sport,
    roi_pct,
    SportSummary,
    write_report,
)


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------


def _picks_log(rows: list[dict[str, Any]]) -> dict:
    return {"picks": rows}


def _pick(
    *,
    model_prob: float,
    result: str,
    units: float,
) -> dict[str, Any]:
    return {
        "model_prob": model_prob,
        "result": result,
        "units": units,
    }


def _write_picks(tmp: Path, sport: str, rows: list[dict[str, Any]]) -> None:
    sport_dir = tmp / sport
    sport_dir.mkdir(parents=True, exist_ok=True)
    (sport_dir / "picks_log.json").write_text(
        json.dumps(_picks_log(rows)),
    )


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------


def test_brier_score_perfect_calibration():
    rows = [
        PickRow("mlb", 1.0, "WIN",  1.0),
        PickRow("mlb", 0.0, "LOSS", -1.0),
    ]
    score, n = brier_score(rows)
    assert score == 0.0
    assert n == 2


def test_brier_score_worst_calibration():
    rows = [
        PickRow("mlb", 0.0, "WIN", 1.0),
        PickRow("mlb", 1.0, "LOSS", -1.0),
    ]
    score, n = brier_score(rows)
    assert score == 1.0


def test_brier_score_excludes_pushes_and_pending():
    rows = [
        PickRow("mlb", 0.6, "WIN",  1.0),
        PickRow("mlb", 0.6, "PUSH", 0.0),
        PickRow("mlb", 0.6, None,   None),
    ]
    score, n = brier_score(rows)
    assert n == 1
    assert score == round((0.6 - 1.0) ** 2, 4)


def test_brier_score_empty_returns_none():
    score, n = brier_score([])
    assert score is None
    assert n == 0


def test_roi_pct_lookback_window():
    """Only the last `lookback` graded picks count toward ROI."""
    rows = [PickRow("mlb", 0.5, "LOSS", -1.0) for _ in range(10)]
    rows.append(PickRow("mlb", 0.5, "WIN", 0.95))
    score, n = roi_pct(rows, lookback=1)
    assert n == 1
    assert score == 95.0


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def test_classify_no_data_below_min_sample():
    s = SportSummary(sport="wnba", brier=0.30, roi_pct=-5.0, n=10)
    assert classify_sport(s) == "no_data"


def test_classify_ok_when_brier_below_gate_and_roi_positive():
    s = SportSummary(
        sport="mlb", brier=0.230, roi_pct=2.0, n=300,
    )
    assert classify_sport(s) == "ok"


def test_classify_warning_when_brier_above_gate():
    s = SportSummary(
        sport="mlb", brier=PUBLISH_GATE_BRIER + 0.001, roi_pct=2.0, n=300,
    )
    assert classify_sport(s) == "warning"


def test_classify_critical_when_brier_far_above_gate():
    s = SportSummary(
        sport="mlb", brier=0.27, roi_pct=2.0, n=300,
    )
    assert classify_sport(s) == "critical"


def test_classify_critical_when_roi_severely_negative():
    s = SportSummary(
        sport="mlb", brier=0.230, roi_pct=-5.0, n=300,
    )
    assert classify_sport(s) == "critical"


def test_classify_warning_when_roi_negative_but_brier_ok():
    s = SportSummary(
        sport="mlb", brier=0.230, roi_pct=-1.0, n=300,
    )
    assert classify_sport(s) == "warning"


# ---------------------------------------------------------------------------
# Alert builder
# ---------------------------------------------------------------------------


def test_build_alerts_emits_brier_alert_when_warning():
    s = SportSummary(
        sport="wnba", brier=0.252, roi_pct=2.0, n=200, status="warning",
    )
    alerts = build_alerts([s])
    assert len(alerts) == 1
    a = alerts[0]
    assert a.sport == "wnba"
    assert a.metric == "brier"
    assert a.level == "warning"
    assert a.threshold == PUBLISH_GATE_BRIER
    assert "WNBA Brier" in a.message


def test_build_alerts_emits_brier_alert_when_critical():
    s = SportSummary(
        sport="nfl", brier=0.275, roi_pct=2.0, n=200, status="critical",
    )
    alerts = build_alerts([s])
    assert len(alerts) == 1
    assert alerts[0].level == "critical"


def test_build_alerts_pairs_brier_and_roi_when_both_drift():
    s = SportSummary(
        sport="ncaaf", brier=0.255, roi_pct=-1.0, n=200,
        status="warning",
    )
    alerts = build_alerts([s])
    metrics = sorted(a.metric for a in alerts)
    assert metrics == ["brier", "roi"]


def test_build_alerts_silent_when_status_ok():
    s = SportSummary(
        sport="mlb", brier=0.230, roi_pct=2.0, n=200, status="ok",
    )
    alerts = build_alerts([s])
    assert alerts == []


# ---------------------------------------------------------------------------
# Loader tolerance
# ---------------------------------------------------------------------------


def test_load_picks_for_sport_missing_file(tmp_path: Path):
    rows = load_picks_for_sport("mlb", data_root=tmp_path)
    assert rows == []


def test_load_picks_for_sport_malformed_json(tmp_path: Path):
    sport_dir = tmp_path / "mlb"
    sport_dir.mkdir(parents=True)
    (sport_dir / "picks_log.json").write_text("{ not json")
    rows = load_picks_for_sport("mlb", data_root=tmp_path)
    assert rows == []


# ---------------------------------------------------------------------------
# build_report — end-to-end
# ---------------------------------------------------------------------------


def test_build_report_no_data_when_picks_missing(tmp_path: Path):
    report = build_report(sports=["mlb", "wnba"], data_root=tmp_path)
    assert report.summary["mlb"]["status"] == "no_data"
    assert report.summary["wnba"]["status"] == "no_data"
    assert report.alerts == []


def test_build_report_fires_warning_when_brier_drifts(tmp_path: Path):
    rows = [
        _pick(model_prob=0.55, result="LOSS", units=-1.0)
        for _ in range(60)
    ]
    rows.extend(
        _pick(model_prob=0.55, result="WIN", units=0.91)
        for _ in range(20)
    )
    _write_picks(tmp_path, "wnba", rows)
    report = build_report(sports=["wnba"], data_root=tmp_path)
    assert report.summary["wnba"]["status"] in ("warning", "critical")
    assert any(a["sport"] == "wnba" for a in report.alerts)


def test_build_report_silent_when_calibrated(tmp_path: Path):
    """A 70% modelled prob that hits 70% of the time → low Brier."""
    n_total = 200
    n_wins = int(n_total * 0.70)
    rows = (
        [_pick(model_prob=0.70, result="WIN", units=1.0)
         for _ in range(n_wins)]
        + [_pick(model_prob=0.70, result="LOSS", units=-1.0)
           for _ in range(n_total - n_wins)]
    )
    _write_picks(tmp_path, "mlb", rows)
    report = build_report(sports=["mlb"], data_root=tmp_path)
    assert report.summary["mlb"]["status"] == "ok"
    assert report.alerts == []


def test_build_report_per_sport_isolation(tmp_path: Path):
    """Drift in one sport doesn't trip the others."""
    # MLB calibrated.
    n = 100
    mlb_rows = (
        [_pick(model_prob=0.6, result="WIN", units=1.0)
         for _ in range(60)]
        + [_pick(model_prob=0.6, result="LOSS", units=-1.0)
           for _ in range(40)]
    )
    _write_picks(tmp_path, "mlb", mlb_rows)
    # WNBA mis-calibrated — claimed 0.7, hit 0.4.
    wnba_rows = (
        [_pick(model_prob=0.7, result="WIN", units=1.0)
         for _ in range(40)]
        + [_pick(model_prob=0.7, result="LOSS", units=-1.0)
           for _ in range(60)]
    )
    _write_picks(tmp_path, "wnba", wnba_rows)
    report = build_report(sports=["mlb", "wnba"], data_root=tmp_path)
    sports_with_alerts = {a["sport"] for a in report.alerts}
    assert "wnba" in sports_with_alerts
    assert "mlb" not in sports_with_alerts


# ---------------------------------------------------------------------------
# write_report — JSON shape contract
# ---------------------------------------------------------------------------


def test_write_report_json_shape(tmp_path: Path):
    report = AlertReport(
        generated_at="2026-05-06T19:32:00Z",
        publish_gate=PUBLISH_GATE_BRIER,
        summary={
            "mlb": {"brier": 0.234, "n": 432, "roi_pct": 2.4, "status": "ok"},
        },
        alerts=[],
    )
    out = tmp_path / "alerts.json"
    write_report(report, out_path=out)
    parsed = json.loads(out.read_text())
    # Required top-level keys.
    assert set(parsed.keys()) >= {
        "version", "generated_at", "publish_gate", "summary", "alerts",
    }
    assert parsed["version"] == 1
    assert parsed["publish_gate"] == PUBLISH_GATE_BRIER
    assert parsed["summary"]["mlb"]["brier"] == 0.234
    assert parsed["summary"]["mlb"]["status"] == "ok"
    assert parsed["alerts"] == []


def test_master_runner_output_paths_include_alerts_json():
    """The master script's `--list-output-paths` must include the
    calibration alerts JSON so the daily-master workflow auto-picks
    it up at commit time."""
    import os
    import subprocess
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(repo_root / "src")
        + os.pathsep + env.get("PYTHONPATH", "")
    )
    out = subprocess.run(
        ["python", "run_daily_all.py", "--list-output-paths"],
        cwd=repo_root, env=env, capture_output=True, text=True, check=True,
    )
    paths = set(line.strip() for line in out.stdout.splitlines() if line.strip())
    assert "website/public/data/calibration_alerts.json" in paths
