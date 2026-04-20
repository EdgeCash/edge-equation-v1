#!/usr/bin/env bash
# push_phase3.sh
#
# Runs apply_phase3.sh, and if tests pass, commits and pushes to
# branch phase-3-engine and prints the PR URL.
#
# Run from the repo root of edge-equation-v1.
#
# Prereqs:
#   - apply_phase3.sh exists in the same folder
#   - Git credentials cached (they are if the scaffold/math PRs worked)
#
# Usage:
#   chmod +x push_phase3.sh
#   ./push_phase3.sh

set -euo pipefail

BRANCH="phase-3-engine"
BASE_BRANCH="main"

# --- 0. Sanity checks ----------------------------------------------------
if [ ! -f "apply_phase3.sh" ]; then
  echo "ERROR: apply_phase3.sh not found in current directory." >&2
  echo "Make sure you're running this from the repo root with both scripts copied in." >&2
  exit 1
fi

# Must be inside a git repo
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not inside a git repository." >&2
  exit 1
fi

# --- 1. Sync with origin/main and create/checkout the branch ------------
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

# --- 2. Apply Phase 3 (writes files + runs tests) ------------------------
echo ""
echo ">> Running apply_phase3.sh"
chmod +x apply_phase3.sh
if ! ./apply_phase3.sh; then
  echo ""
  echo "ERROR: apply_phase3.sh failed (tests did not pass)." >&2
  echo "Nothing committed, nothing pushed." >&2
  exit 1
fi

# --- 3. Commit ----------------------------------------------------------
echo ""
echo ">> Staging and committing"
git add -A

if git diff --cached --quiet; then
  echo "Nothing to commit. Phase 3 may already be in place on this branch."
else
  git commit -m "Add Phase-3 engine pipeline: features, picks, engine, formatter, scheduler

Engine pipeline on top of the Phase-2 math layer:
- src/edge_equation/engine/feature_builder.py
    FeatureBuilder + FeatureBundle dataclass
    Validates sport + market_type against sport_config
    Normalizes universal features (drops unknown keys)
- src/edge_equation/engine/pick_schema.py
    Frozen Pick + Line dataclasses, .to_dict() for portability
- src/edge_equation/engine/betting_engine.py
    BettingEngine.evaluate(bundle, line, public_mode=False) -> Pick
    Routes ML/BTTS/Run_Line/Puck_Line/Spread via fair_prob path
    Routes Total + rate-props via expected_value path
- src/edge_equation/posting/posting_formatter.py
    7 card types: daily_edge, evening_edge, overseas_edge,
    highlighted_game, model_highlight, sharp_signal, the_outlier
    Tagline: 'Facts. Not Feelings.'
- src/edge_equation/engine/daily_scheduler.py
    generate_daily_edge_card / generate_evening_edge_card
    Stubbed game data; no API calls

Tests (formula-consistency, not hand-worked literals):
- tests/test_feature_builder.py
- tests/test_betting_engine.py
- tests/test_posting_formatter.py
- tests/test_daily_scheduler.py

DET @ BOS end-to-end: fair_prob=0.618133, edge=0.049167, grade=A, half-Kelly=0.0324.
All values are whatever the Phase-2 math layer produces."
fi

# --- 4. Push -------------------------------------------------------------
echo ""
echo ">> Pushing $BRANCH to origin"
git push -u origin "$BRANCH"

# --- 5. PR URL -----------------------------------------------------------
echo ""
echo "================================================================"
echo "Push complete. Open the PR by clicking this URL:"
echo ""
echo "  https://github.com/EdgeCash/edge-equation-v1/pull/new/$BRANCH"
echo ""
echo "Suggested PR title:"
echo "  Phase-3 Engine Pipeline: Features, Picks, Engine, Formatter, Scheduler"
echo ""
echo "GitHub will prefill the description from the commit message."
echo "================================================================"
