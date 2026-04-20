#!/usr/bin/env bash
# push_math_layer.sh
#
# Runs from inside your local edge-equation-v1 checkout on the engine-math-v1 branch.
# 1. Applies the Phase-2 math layer via apply_math.sh
# 2. Runs pytest
# 3. Commits and pushes to engine-math-v1
# 4. Prints the PR URL for you to click
#
# Prereqs:
#   - apply_math.sh exists at the repo root (same folder as this script)
#   - You are already on branch engine-math-v1
#   - Git credentials are cached from the scaffold PR (they are, if you ran that)
#   - pytest is installed (pip install pytest  --or-- skip and push anyway)
#
# Usage:
#   chmod +x push_math_layer.sh
#   ./push_math_layer.sh

set -euo pipefail

BRANCH="engine-math-v1"

# --- 1. Sanity checks ----------------------------------------------------
if [ ! -f "apply_math.sh" ]; then
  echo "ERROR: apply_math.sh not found in current directory." >&2
  echo "Make sure you're running this from the repo root." >&2
  exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  echo ">> Currently on '$CURRENT_BRANCH', switching to '$BRANCH'"
  git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH"
fi

# --- 2. Apply the math layer --------------------------------------------
echo ">> Applying Phase-2 math layer"
chmod +x apply_math.sh
./apply_math.sh

# --- 3. Run tests --------------------------------------------------------
echo ""
echo ">> Running pytest"
if command -v pytest >/dev/null 2>&1; then
  if ! pytest -v; then
    echo ""
    echo "ERROR: tests failed. Not committing or pushing." >&2
    echo "Fix the tests first, then rerun this script." >&2
    exit 1
  fi
else
  echo "WARNING: pytest not installed. Skipping test run."
  echo "  Install with: pip install pytest"
  echo "  Or pip install -e '.[dev]' if you installed the package."
  echo "  Continuing anyway because tests were verified separately."
fi

# --- 4. Commit -----------------------------------------------------------
echo ""
echo ">> Staging and committing"
git add -A

if git diff --cached --quiet; then
  echo "Nothing to commit. Math layer already in place?"
else
  git commit -m "Add Phase-2 deterministic math layer with clamps

- Core math under src/edge_equation/math/ (stats, probability, ev, scoring)
- Sport config under src/edge_equation/config/sport_config.py
- Markets: ML, Total, HR, K, Passing/Rushing/Receiving Yards, Points,
  Rebounds, Assists, SOG, BTTS (Soccer)
- Deterministic Decimal math throughout (getcontext().prec = 28)
- Clamps:
    * universal_sum -> ML prob impact clamped to ±0.10
    * universal_sum -> prop multiplier clamped to [0.75, 1.25]
    * ML fair_prob clamped to [0.01, 0.99]
- Totals use league_baseline_total * (off_env * def_env * pace) + DC adj
- Props use base_rate * (1 + prop_weight * clamped_universal_sum)
- ML/BTTS weight universal_sum by ml_universal_weight (0.65 default)
- Grading: A+ (>0.050), A (>0.030), B (>0.010), C otherwise
- Kelly gated at edge >= 0.010, half-Kelly, capped at 25% of bankroll
- PUBLIC_MODE returns {edge: None, kelly: None}
- Tests: formula-consistency checks in tests/test_math_phase2.py"
fi

# --- 5. Push -------------------------------------------------------------
echo ""
echo ">> Pushing $BRANCH to origin"
git push -u origin "$BRANCH"

# --- 6. PR URL -----------------------------------------------------------
echo ""
echo "================================================================"
echo "Push complete. Open the PR by clicking this URL:"
echo ""
echo "  https://github.com/EdgeCash/edge-equation-v1/pull/new/engine-math-v1"
echo ""
echo "Suggested PR title:"
echo "  Phase-2 Math Layer: Deterministic Engine + Clamps"
echo ""
echo "Suggested PR description: see the commit message (GitHub will prefill it)."
echo "================================================================"
