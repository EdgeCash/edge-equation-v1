#!/usr/bin/env bash
# Re-sync verbatim modules from EdgeCash/edge-equation-scrapers into
# edge_equation/exporters/mlb/.
#
# Usage:
#     ./scripts/sync_scrapers_modules.sh             # default: main branch
#     ./scripts/sync_scrapers_modules.sh COMMIT_SHA  # pin to a specific commit
#
# After running this, verify with:
#     PYTHONPATH=src python -c "from edge_equation.exporters.mlb import \\
#         park_factors, clv_tracker, splits_loader, backtest, closing_snapshot"
# and re-apply the import-path patches documented in
# docs/MLB_PIPELINE_CUTOVER.md (REPO_ROOT, exporters.mlb.* -> edge_equation.exporters.mlb.*,
# scrapers.mlb.mlb_odds_scraper -> v1 ingestion shim).
#
# Note: isotonic.py is NOT synced — v1's canonical implementation at
# edge_equation.math.isotonic is used instead (scrapers' was a back-port).

set -euo pipefail

REF="${1:-main}"
BASE="https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/${REF}/exporters/mlb"
DEST="src/edge_equation/exporters/mlb"

if [[ ! -d "$DEST" ]]; then
    echo "error: $DEST not found — run from repo root" >&2
    exit 1
fi

MODULES=(
    park_factors.py
    closing_snapshot.py
    clv_tracker.py
    splits_loader.py
    backtest.py
)

echo "Syncing ${#MODULES[@]} modules from edge-equation-scrapers@${REF}"
for m in "${MODULES[@]}"; do
    url="${BASE}/${m}"
    out="${DEST}/${m}"
    echo "  ${m}"
    curl -fsSL --max-time 30 "$url" -o "$out"
done

echo
echo "Sync complete. Now re-apply import-path patches — see"
echo "docs/MLB_PIPELINE_CUTOVER.md \"Stub-replacement script\" section"
echo "for the exact edits."
