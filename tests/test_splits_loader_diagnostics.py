"""
Diagnostic-path tests for SplitsLoader.

The original silent failure ("0/24 probable SPs got prior-season xwOBA
data" with no explanation) wasted operator time tracing where the data
was supposed to come from. These tests pin the diagnostic behavior so
future regressions reintroducing the silence will fail loud.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from edge_equation.exporters.mlb.splits_loader import SplitsLoader


def test_missing_backfill_dir_records_no_paths_until_loaded(tmp_path: Path):
    sl = SplitsLoader(tmp_path / "nope")
    # No lookups attempted yet → no records.
    assert sl.diagnostic_report() == {
        "missing_files": [],
        "backfill_dir": str(tmp_path / "nope"),
        "backfill_dir_exists": False,
    }


def test_missing_xstats_logs_once_and_records_path(tmp_path: Path, caplog):
    sl = SplitsLoader(tmp_path)
    with caplog.at_level(logging.WARNING, logger="edge_equation.exporters.mlb.splits_loader"):
        # Three lookups for season 2026 → load 2025 xstats. File missing.
        for pid in (1, 2, 3):
            assert sl.pitcher_xwoba(pid, 2026) is None
    # Logged exactly once despite three lookups (per-path dedup).
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "statcast_xstats" in msg
    assert "2025/statcast_xstats.json" in msg
    # Diagnostic report surfaces the same.
    report = sl.diagnostic_report()
    assert len(report["missing_files"]) == 1
    rec = report["missing_files"][0]
    assert rec["kind"] == "statcast_xstats"
    assert rec["path"].endswith("2025/statcast_xstats.json")


def test_missing_splits_and_xstats_record_separately(tmp_path: Path):
    sl = SplitsLoader(tmp_path)
    # Trigger both data layers:
    sl.hitter_avg_vs(123, 2026, "L")     # → splits
    sl.pitcher_xwoba(456, 2026)          # → statcast_xstats
    report = sl.diagnostic_report()
    kinds = {r["kind"] for r in report["missing_files"]}
    assert "splits" in kinds
    assert "statcast_xstats" in kinds


def test_present_xstats_does_not_record_missing(tmp_path: Path):
    season_dir = tmp_path / "2025"
    season_dir.mkdir()
    payload = {
        "season": 2025,
        "pitching": {
            "543037": {"pa": 600, "xwoba": 0.295, "xba": 0.230, "xslg": 0.380},
        },
        "batting": {},
    }
    (season_dir / "statcast_xstats.json").write_text(json.dumps(payload))
    sl = SplitsLoader(tmp_path)
    # Real lookup hits the file successfully.
    val = sl.pitcher_xwoba(543037, 2026)
    assert val == 0.295
    # No missing-paths recorded for the xstats kind.
    kinds = {r["kind"] for r in sl.diagnostic_report()["missing_files"]}
    assert "statcast_xstats" not in kinds


def test_below_threshold_returns_none_without_logging_missing(tmp_path: Path):
    """A player below MIN_XSTATS_PA is a different failure mode than 'file
    missing.' The loader returns None but does NOT record a missing-file
    entry — the file is fine, the sample size just isn't there."""
    season_dir = tmp_path / "2025"
    season_dir.mkdir()
    (season_dir / "statcast_xstats.json").write_text(json.dumps({
        "season": 2025,
        "pitching": {
            "999": {"pa": 50, "xwoba": 0.310, "xba": 0.240, "xslg": 0.400},
        },
        "batting": {},
    }))
    sl = SplitsLoader(tmp_path)
    assert sl.pitcher_xwoba(999, 2026) is None
    kinds = {r["kind"] for r in sl.diagnostic_report()["missing_files"]}
    assert "statcast_xstats" not in kinds


def test_corrupt_xstats_records_corrupt_kind(tmp_path: Path, caplog):
    season_dir = tmp_path / "2025"
    season_dir.mkdir()
    (season_dir / "statcast_xstats.json").write_text("{ this is not json")
    sl = SplitsLoader(tmp_path)
    with caplog.at_level(logging.WARNING, logger="edge_equation.exporters.mlb.splits_loader"):
        assert sl.pitcher_xwoba(1, 2026) is None
    rec = sl.diagnostic_report()["missing_files"][0]
    assert "corrupt" in rec["kind"]


def test_diagnostic_report_dedups_same_path_across_kinds(tmp_path: Path):
    """Each missing path is recorded exactly once, even if many lookups
    target the same file. Keeps the per-slate summary readable."""
    sl = SplitsLoader(tmp_path)
    # 100 lookups, all hitting the same missing 2025/statcast_xstats.json
    for pid in range(100):
        sl.pitcher_xwoba(pid, 2026)
    files = sl.diagnostic_report()["missing_files"]
    paths = {r["path"] for r in files}
    assert len(paths) == 1
