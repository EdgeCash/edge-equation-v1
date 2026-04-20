#!/usr/bin/env bash
# push_phase4.sh
#
# Runs apply_phase4.sh, and if tests pass, commits and pushes to
# branch phase-4a-ingestion and prints the PR URL.
#
# Run from the repo root of edge-equation-v1.
#
# Prereqs:
#   - apply_phase4.sh exists in the same folder
#   - Git credentials cached

set -euo pipefail

BRANCH="phase-4a-ingestion"
BASE_BRANCH="main"

if [ ! -f "apply_phase4.sh" ]; then
  echo "ERROR: apply_phase4.sh not found in current directory." >&2
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not inside a git repository." >&2
  exit 1
fi

echo ">> Fetching latest from origin"
git fetch origin

echo ">> Ensuring $BASE_BRANCH is up to date"
git checkout "$BASE_BRANCH"
git pull origin "$BASE_BRANCH"

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  echo ">> Branch $BRANCH exists locally, checking out"
  git checkout "$BRANCH"
else
  echo ">> Creating branch $BRANCH from $BASE_BRANCH"
  git checkout -b "$BRANCH"
fi

echo ""
echo ">> Running apply_phase4.sh"
chmod +x apply_phase4.sh
if ! ./apply_phase4.sh; then
  echo ""
  echo "ERROR: apply_phase4.sh failed." >&2
  exit 1
fi

echo ""
echo ">> Staging and committing"
git add -A

if git diff --cached --quiet; then
  echo "Nothing to commit. Phase 4A may already be in place on this branch."
else
  git commit -m "Phase 4A – ingestion layer

Adds deterministic, schema-validated ingestion on top of the Phase-3 engine.

Modules:
- src/edge_equation/ingestion/schema.py
    Frozen GameInfo, MarketInfo, Slate dataclasses with .to_dict()
- src/edge_equation/ingestion/normalizer.py
    Raw dicts -> typed Slate; enforces league/market validity
- src/edge_equation/ingestion/base_source.py
    BaseSource Protocol (no network, no randomness)
- src/edge_equation/ingestion/mlb_source.py
    MlbLikeSource covering MLB, KBO, NPB
- src/edge_equation/ingestion/nba_source.py
- src/edge_equation/ingestion/nhl_source.py
- src/edge_equation/ingestion/nfl_source.py
- src/edge_equation/ingestion/soccer_source.py
- src/edge_equation/ingestion/odds_source.py
    american_to_implied_prob / implied_prob_to_american
    Matches Phase-2 EVCalculator conventions exactly.
- src/edge_equation/engine/slate_runner.py
    run_slate(slate, sport) -> list[Pick]
    Glues ingestion to the Phase-3 engine. Gracefully skips markets
    whose math routes aren't yet wired (Run_Line, Puck_Line, Spread,
    NRFI/YRFI) instead of raising.

Tests (formula-consistency):
- tests/test_ingestion_schema.py
- tests/test_ingestion_normalizer.py
- tests/test_ingestion_sources.py
- tests/test_slate_runner_integration.py

All sources are deterministic mock data. No API calls."
fi

echo ""
echo ">> Pushing $BRANCH to origin"
git push -u origin "$BRANCH"

echo ""
echo "================================================================"
echo "Push complete. Open the PR by clicking this URL:"
echo ""
echo "  https://github.com/EdgeCash/edge-equation-v1/pull/new/$BRANCH"
echo ""
echo "Suggested PR title:"
echo "  Phase-4A Ingestion Layer: Schema, Normalizer, Sport Sources, Slate Runner"
echo ""
echo "GitHub will prefill the description from the commit message."
echo "================================================================"
