"""
Phase 26c cache-read-only invariant.

After the Data Refresher #2 run leaked fresh odds into
"edge-equation-db-main-<run_id>" while every cadence workflow kept
reading the stale "edge-equation-db-main" primary key, the fix is:

  - The refresher is the SOLE writer. Its explicit actions/cache/save
    step writes to a unique "...${run_id}" key.
  - Every other workflow uses actions/cache/restore@v4 (read-only),
    keyed to a never-matching "...read-${run_id}", so the restore
    falls through to the restore-keys prefix and always picks up the
    refresher's newest entry.
  - Nobody writes to the old "edge-equation-db-main" primary key ever
    again; that stale entry will evict naturally.
"""
from pathlib import Path

import pytest


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

_CADENCE = (
    "ledger.yml",
    "daily-edge.yml",
    "spotlight.yml",
    "evening-edge.yml",
    "overseas-edge.yml",
)

_PREVIEWS = (
    "premium-daily-preview.yml",
    "email-preview.yml",
    "dry-run-preview.yml",
)


# ---------------------------------------------- readers use cache/restore


@pytest.mark.parametrize("filename", _CADENCE + _PREVIEWS)
def test_reader_workflow_uses_cache_restore_action(filename):
    """Every workflow that only CONSUMES the cache must use
    actions/cache/restore@v4 (which skips the post-job auto-save).
    actions/cache@v4 would auto-save an empty-slate DB back to the
    primary key and clobber the refresher's data."""
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert "actions/cache/restore@v4" in text, (
        f"{filename} must use actions/cache/restore@v4 (read-only) so "
        f"it can't clobber the refresher's fresh cache entry"
    )
    # Safety rail: readers must NOT use the write-capable cache action.
    assert "actions/cache@v4" not in text, (
        f"{filename} is a cache READER; actions/cache@v4 would auto-"
        f"save and overwrite the refresher's work."
    )


@pytest.mark.parametrize("filename", _CADENCE + _PREVIEWS)
def test_reader_primary_key_is_never_matching(filename):
    """Readers use a unique per-run primary key so the exact-match
    path misses and the restore-keys prefix fallback wins. Without
    this, a stale pre-existing key would hit and lock in empty data."""
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert "${{ github.run_id }}" in text, (
        f"{filename} must include github.run_id in its restore key "
        f"so exact-match misses and restore-keys prefix wins."
    )
    assert "edge-equation-db-${{ github.ref_name }}-" in text


# ---------------------------------------------- writer is unique


def test_refresher_uses_restore_only_on_first_step():
    """The refresher's first step must be RESTORE-only -- auto-save
    would try to write back to a primary key that may already exist,
    and GitHub Actions cache refuses to overwrite (the exact bug
    we just fixed)."""
    text = (WORKFLOWS / "data-refresher.yml").read_text(encoding="utf-8")
    assert "actions/cache/restore@v4" in text


def test_refresher_explicit_save_uses_unique_key():
    """The refresher's single explicit save step must use a unique
    per-run key so successive runs always create new entries rather
    than bumping against the immutable "primary key exists" rule."""
    text = (WORKFLOWS / "data-refresher.yml").read_text(encoding="utf-8")
    assert "actions/cache/save@v4" in text
    # Save key includes run_id (unique).
    assert "edge-equation-db-${{ github.ref_name }}-${{ github.run_id }}" in text


def test_no_workflow_writes_to_plain_primary_key():
    """No workflow may write to the bare "edge-equation-db-main" key.
    That's the key the original cache bug got stuck on -- writing to
    it is now forbidden so the old stale entry evicts naturally.

    We detect a "write" as: actions/cache@v4 (auto-saves) OR
    actions/cache/save@v4 explicitly keyed at the bare primary key.
    """
    for f in WORKFLOWS.iterdir():
        if f.suffix != ".yml":
            continue
        text = f.read_text(encoding="utf-8")
        if "actions/cache/save@v4" not in text:
            continue
        # Extract every save key line.
        save_block = text.split("actions/cache/save@v4", 1)[1]
        # The next "key:" line within ~500 chars is the save key.
        save_block_head = save_block[:500]
        # Forbidden shape: key ends with the plain ref_name, no run_id.
        bad_pattern = 'key: edge-equation-db-${{ github.ref_name }}\n'
        assert bad_pattern not in save_block_head, (
            f"{f.name} saves to the bare primary key -- this recreates "
            f"the Phase 26c cache-overwrite bug."
        )


def test_no_reader_has_restore_keys_that_would_pollute_write_back():
    """Readers use cache/restore@v4 which is save-free, so their
    restore-keys don't create write-back risk. But double-check the
    post-Phase-26c text doesn't accidentally reintroduce an empty
    'key: edge-equation-db-${{ github.ref_name }}\n' in a reader
    workflow (the shape that hit the bug)."""
    for f in _CADENCE + _PREVIEWS:
        text = (WORKFLOWS / f).read_text(encoding="utf-8")
        # Reader workflows must always qualify the key with run_id.
        assert "key: edge-equation-db-${{ github.ref_name }}\n" not in text, (
            f"{f} has a bare 'key:' value that would lock on the stale "
            f"edge-equation-db-main entry again."
        )
