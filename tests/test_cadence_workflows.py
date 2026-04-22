"""
Phase 21: GitHub Actions cadence workflow integrity.

The five mandatory windows (9a / 11a / 4p / 6p / 11p CT) each have a
workflow file that:
  - uses dual cron lines (one for CST, one for CDT UTC offsets)
  - runs a "CT-hour guard" step that calls zoneinfo and only proceeds on
    the cron that matches the current DST state
  - invokes the matching `python -m edge_equation <subcommand>` at the end

We don't execute the workflows in this test -- we parse the YAML as text
and assert the invariants that must hold so the five slots can't drift
apart from the cadence in src/edge_equation/posting/cadence.py.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


# Cadence spec (must match src/edge_equation/posting/cadence.py).
# Each entry: (workflow_filename, hour_ct, subcommand)
CADENCE = [
    ("ledger.yml", 9, "ledger"),
    ("daily-edge.yml", 11, "daily"),
    ("spotlight.yml", 16, "spotlight"),
    ("evening-edge.yml", 18, "evening"),
    ("overseas-edge.yml", 23, "overseas"),
]


def _read(path: str) -> str:
    return (WORKFLOWS_DIR / path).read_text(encoding="utf-8")


def _expected_utc_pair(hour_ct: int):
    """Return (CDT_utc_hour, CST_utc_hour) for the given CT hour."""
    ct = ZoneInfo("America/Chicago")
    utc = ZoneInfo("UTC")
    # July = CDT (UTC-5), January = CST (UTC-6)
    summer = datetime(2026, 7, 15, hour_ct, 0, tzinfo=ct).astimezone(utc).hour
    winter = datetime(2026, 1, 15, hour_ct, 0, tzinfo=ct).astimezone(utc).hour
    return summer, winter


@pytest.mark.parametrize("filename,hour_ct,subcommand", CADENCE)
def test_each_workflow_file_exists(filename, hour_ct, subcommand):
    assert (WORKFLOWS_DIR / filename).is_file(), f"missing workflow: {filename}"


@pytest.mark.parametrize("filename,hour_ct,subcommand", CADENCE)
def test_workflow_has_dual_cron_for_cst_and_cdt(filename, hour_ct, subcommand):
    text = _read(filename)
    cdt_h, cst_h = _expected_utc_pair(hour_ct)
    # Both UTC-hour cron lines must appear so neither half of the year is missed.
    assert f'cron: "0 {cdt_h} * * *"' in text, (
        f"{filename} missing CDT cron hour {cdt_h}"
    )
    assert f'cron: "0 {cst_h} * * *"' in text, (
        f"{filename} missing CST cron hour {cst_h}"
    )


@pytest.mark.parametrize("filename,hour_ct,subcommand", CADENCE)
def test_workflow_has_ct_hour_guard(filename, hour_ct, subcommand):
    text = _read(filename)
    assert 'ZoneInfo("America/Chicago")' in text, (
        f"{filename} missing zoneinfo CT-hour guard"
    )
    assert f"expected = {hour_ct}" in text, (
        f"{filename} must guard on CT hour {hour_ct}"
    )
    assert "should_run" in text, f"{filename} must emit should_run output"


@pytest.mark.parametrize("filename,hour_ct,subcommand", CADENCE)
def test_workflow_invokes_correct_cli_subcommand(filename, hour_ct, subcommand):
    text = _read(filename)
    assert f"python -m edge_equation {subcommand}" in text, (
        f"{filename} must invoke `python -m edge_equation {subcommand}`"
    )


@pytest.mark.parametrize("filename,hour_ct,subcommand", CADENCE)
def test_workflow_passes_public_mode_flag(filename, hour_ct, subcommand):
    text = _read(filename)
    # Every scheduled post is free-content; public_mode must be ON so the
    # disclaimer + Season Ledger footer are injected.
    assert "--public-mode" in text, (
        f"{filename} must publish with --public-mode"
    )


def test_cadence_matches_module_spec():
    from edge_equation.posting.cadence import CADENCE_WINDOWS
    hours_in_module = [s.hour_ct for s in CADENCE_WINDOWS]
    hours_in_workflows = [h for _, h, _ in CADENCE]
    assert hours_in_module == hours_in_workflows
