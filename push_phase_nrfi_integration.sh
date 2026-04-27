#!/usr/bin/env bash
# push_phase_nrfi_integration.sh
#
# Validates the NRFI integration phase, runs tests, commits, and pushes
# to branch `claude/nrfi-integration-phase2`. Prints the PR URL.
#
# Run from the repo root.

set -euo pipefail

BRANCH="claude/nrfi-integration-phase2"
BASE_BRANCH="main"

if [ ! -f "apply_phase_nrfi_integration.sh" ]; then
  echo "ERROR: apply_phase_nrfi_integration.sh not found in current directory." >&2
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not inside a git repository." >&2
  exit 1
fi

echo "[phase-nrfi-push] Running apply_phase_nrfi_integration.sh sanity gate..."
bash apply_phase_nrfi_integration.sh

echo "[phase-nrfi-push] Running pytest (if present)..."
if command -v pytest >/dev/null 2>&1; then
  if [ -f tests/test_nrfi_integration.py ]; then
    pytest -q tests/test_nrfi_integration.py || {
      echo "ERROR: tests failed — fix before pushing." >&2
      exit 1
    }
  fi
fi

echo "[phase-nrfi-push] Switching to branch $BRANCH..."
if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
  git checkout "$BRANCH"
else
  git checkout -b "$BRANCH"
fi

echo "[phase-nrfi-push] Staging changes..."
git add src/edge_equation/engines/nrfi/integration src/edge_equation/engines/nrfi/data/team_splits.py src/edge_equation/engines/nrfi/data/lineups.py \
        src/edge_equation/engines/nrfi/features/feature_engineering.py \
        src/edge_equation/engines/nrfi/evaluation/backtest.py src/edge_equation/engines/nrfi/backtest_historical.py \
        src/edge_equation/ingestion/mlb_nrfi_source.py \
        src/edge_equation/ingestion/source_factory.py \
        src/edge_equation/posting/nrfi_card.py \
        api/routers/nrfi.py api/main.py \
        pyproject.toml \
        apply_phase_nrfi_integration.sh push_phase_nrfi_integration.sh \
        tests/test_nrfi_integration.py 2>/dev/null || true

if git diff --cached --quiet; then
  echo "[phase-nrfi-push] No staged changes — nothing to commit."
  exit 0
fi

git commit -m "Phase NRFI Integration: wire elite NRFI engine into pipeline

- Add src/edge_equation/engines/nrfi/integration/ bridge (shrinkage, calibration, grading,
  engine_bridge) — the only surface src/edge_equation/ imports from
  the NRFI subsystem.
- Add Tango-style empirical-Bayes shrinkage to feature inputs
  (top-of-order OBP, pitcher ERA/FIP/K%/BB%) using existing
  exponential-decay knobs as the precedent.
- Bake in 2026 ABS Challenge System priors (overturn 0.54, catcher
  0.64, walk-rate 0.099) and shift pitcher BB% upward in the ABS era.
- Auto-switch ABS toggle in backtest replay based on season; report
  pre-ABS vs ABS-era metrics side by side.
- Add lineup fallback chain (confirmed → projected → modal-recent →
  default) with point-in-time guard for backtest safety.
- Add TeamRankings scraper + actuals-derived path for team
  first-inning splits.
- Add pitch-arsenal feature block (FB velo/spin/movement, secondary
  whiff%, CSW%, zone%, chase%).
- Add src/edge_equation/ingestion/mlb_nrfi_source.py, register in
  source_factory.nrfi_source_for_league() — additive layer that
  emits engine-backed NRFI/YRFI rows alongside the standard MLB feed.
- Add src/edge_equation/posting/nrfi_card.py — text/dict renderer
  for the premium daily email and dashboard payload.
- Add api/routers/nrfi.py mounted at /src/edge_equation/engines/nrfi/{today,board}.
- Update pyproject.toml with [project.optional-dependencies].nrfi
  extras so the elite stack installs via 'pip install -e .[nrfi]'.

No changes to deterministic-core math; all new code is import-time
optional and gracefully degrades when the [nrfi] extras are absent."

echo "[phase-nrfi-push] Pushing to origin/$BRANCH..."
git push -u origin "$BRANCH"

echo "================================================================"
echo "Push complete. Open the PR by clicking this URL:"
echo ""
echo "  https://github.com/EdgeCash/edge-equation-v1/pull/new/$BRANCH"
echo ""
echo "Suggested PR title:"
echo "  Phase NRFI Integration: wire elite NRFI engine into pipeline"
echo "================================================================"
