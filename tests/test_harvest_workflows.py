"""Structural tests for the harvest-* GitHub Actions workflows.

We can't actually run a workflow from CI's CI, but we can pin the
contract so an accidental edit can't break:

  * Every harvest workflow is `workflow_dispatch`-only (manual trigger).
  * Each invokes a real script under `scripts/`.
  * Each declares `permissions: contents: write` since they push back.
  * The dispatcher's matrix covers exactly the per-league workflows.

Keeps PyYAML as the only test-time dep -- already in dev extras.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
HARVEST_GLOB = "harvest-*.yml"


def _on(workflow: dict):
    """PyYAML maps the bare key `on:` to Python True, so accept either form."""
    return workflow.get("on") or workflow.get(True)


@pytest.fixture(scope="module")
def harvest_files() -> list[Path]:
    files = sorted(WORKFLOW_DIR.glob(HARVEST_GLOB))
    assert files, "No harvest-*.yml workflows found"
    return files


@pytest.fixture(scope="module")
def harvest_workflows(harvest_files) -> dict[str, dict]:
    return {p.name: yaml.safe_load(p.read_text()) for p in harvest_files}


# ---------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------

def test_every_harvest_workflow_is_manual_only(harvest_workflows):
    for name, wf in harvest_workflows.items():
        triggers = _on(wf)
        assert isinstance(triggers, dict), f"{name}: missing 'on:' block"
        assert "workflow_dispatch" in triggers, (
            f"{name}: harvest workflows must be manual-only "
            f"(no schedule:, no push:)"
        )
        # Specifically -- catch the regression we cleaned up in PR #153.
        assert "push" not in triggers, (
            f"{name}: harvest workflow must not auto-trigger on push"
        )
        assert "schedule" not in triggers, (
            f"{name}: harvest workflow must not auto-trigger on schedule"
        )


def test_every_harvest_workflow_can_commit(harvest_workflows):
    for name, wf in harvest_workflows.items():
        perms = wf.get("permissions") or {}
        assert perms.get("contents") == "write", (
            f"{name}: needs `permissions.contents: write` to push backfill"
        )


def test_every_harvest_workflow_has_a_job(harvest_workflows):
    for name, wf in harvest_workflows.items():
        jobs = wf.get("jobs") or {}
        assert jobs, f"{name}: no jobs defined"


# ---------------------------------------------------------------------
# Per-league workflows -> backing scripts
# ---------------------------------------------------------------------

PER_LEAGUE_SCRIPTS = {
    "harvest-mlb-props.yml":         "scripts/backfill_player_games.py",
    "harvest-nba-games.yml":         "scripts/backfill_nba_games.py",
    "harvest-nhl-games.yml":         "scripts/backfill_nhl_games.py",
    "harvest-wnba-player-games.yml": "scripts/backfill_wnba_player_games.py",
}


@pytest.mark.parametrize("workflow_name,script_path", list(PER_LEAGUE_SCRIPTS.items()))
def test_per_league_workflow_invokes_real_script(workflow_name, script_path):
    p = WORKFLOW_DIR / workflow_name
    assert p.exists(), f"{workflow_name} missing"
    body = p.read_text()
    assert script_path in body, (
        f"{workflow_name} doesn't reference {script_path}"
    )
    assert (REPO_ROOT / script_path).exists(), (
        f"{script_path} referenced by {workflow_name} but not on disk"
    )


# ---------------------------------------------------------------------
# Dispatcher matrix integrity
# ---------------------------------------------------------------------

def test_dispatcher_matrix_covers_each_league_script():
    p = WORKFLOW_DIR / "harvest-all-leagues.yml"
    assert p.exists()
    body = p.read_text()
    for script in PER_LEAGUE_SCRIPTS.values():
        assert script in body, (
            f"dispatcher missing reference to {script}"
        )


def test_dispatcher_resolve_step_handles_every_league_label():
    """The dispatcher's `case` arms must cover the same league labels
    its plan job emits. Catches drift between the matrix labels and
    the resolver."""
    body = (WORKFLOW_DIR / "harvest-all-leagues.yml").read_text()
    for label in ("mlb-props", "nba", "nhl", "wnba-player-games"):
        assert f"{label})" in body, (
            f"dispatcher's `case` is missing arm for league '{label}'"
        )


# ---------------------------------------------------------------------
# Common quality bars
# ---------------------------------------------------------------------

def test_every_harvest_workflow_uploads_artifact(harvest_workflows):
    """Even when the commit step skips (commit=false), the artifact
    upload guarantees the operator can inspect the data."""
    for name, wf in harvest_workflows.items():
        rendered = yaml.safe_dump(wf)
        assert "actions/upload-artifact" in rendered, (
            f"{name}: missing artifact upload"
        )


def test_every_harvest_workflow_is_resume_safe(harvest_workflows):
    """Push step must rebase+retry so concurrent harvests don't race."""
    for name, wf in harvest_workflows.items():
        text = (WORKFLOW_DIR / name).read_text()
        assert "git pull --rebase" in text, (
            f"{name}: push step must rebase before retry"
        )
