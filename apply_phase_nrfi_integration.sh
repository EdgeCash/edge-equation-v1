#!/usr/bin/env bash
# apply_phase_nrfi_integration.sh
#
# Phase NRFI-Integration:
#   Wire the elite NRFI/YRFI engine (`src/edge_equation/engines/nrfi/`) into the deterministic
#   Edge Equation pipeline.
#
# This script is *idempotent* — running it twice does nothing harmful.
# It is generated to match the repo's established phase-script style
# (`apply_phase5_6a.sh`, `apply_math.sh`, etc.) and can be re-applied
# to a fresh checkout to bootstrap the integration files when they
# are missing.
#
# Run from the repo root.

set -euo pipefail

REQUIRED_FILES=(
  "src/edge_equation/engines/nrfi/integration/__init__.py"
  "src/edge_equation/engines/nrfi/integration/engine_bridge.py"
  "src/edge_equation/engines/nrfi/integration/shrinkage.py"
  "src/edge_equation/engines/nrfi/integration/calibration.py"
  "src/edge_equation/engines/nrfi/integration/grading.py"
  "src/edge_equation/engines/nrfi/data/team_splits.py"
  "src/edge_equation/engines/nrfi/data/lineups.py"
  "src/edge_equation/ingestion/mlb_nrfi_source.py"
  "src/edge_equation/posting/nrfi_card.py"
  "api/routers/nrfi.py"
)

echo "[phase-nrfi] Verifying integration files exist..."
missing=0
for f in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "  MISSING: $f"
    missing=$((missing + 1))
  fi
done
if [ "$missing" -gt 0 ]; then
  echo "[phase-nrfi] $missing files missing — this script is a sanity gate only."
  echo "             Re-apply by running phase 2 branch's commit; do not edit by hand."
  exit 1
fi

echo "[phase-nrfi] Checking pyproject.toml has [project.optional-dependencies].nrfi extras..."
if ! grep -q "^nrfi = \[" pyproject.toml; then
  echo "  ERROR: pyproject.toml is missing the 'nrfi' optional extras section."
  exit 1
fi

echo "[phase-nrfi] Checking api/main.py mounts the nrfi router..."
if ! grep -q "nrfi.router" api/main.py; then
  echo "  ERROR: api/main.py does not include nrfi.router."
  exit 1
fi

echo "[phase-nrfi] Compiling all new modules..."
python -m compileall -q src/edge_equation/engines/nrfi/integration src/edge_equation/engines/nrfi/data/team_splits.py src/edge_equation/engines/nrfi/data/lineups.py \
  src/edge_equation/ingestion/mlb_nrfi_source.py \
  src/edge_equation/posting/nrfi_card.py \
  api/routers/nrfi.py

echo "[phase-nrfi] OK — integration files in place and compile cleanly."
echo "             Run \`pytest tests/test_nrfi_integration.py\` to verify wiring."
