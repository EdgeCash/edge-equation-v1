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

EXPORTER_MODULES=(
    park_factors.py
    closing_snapshot.py
    clv_tracker.py
    splits_loader.py
    backtest.py
)

# Game-data scrapers live in a sibling package in v1
# (src/edge_equation/scrapers/mlb/ vs scrapers' flat scrapers/mlb/).
SCRAPER_BASE="https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/${REF}/scrapers/mlb"
SCRAPER_DEST="src/edge_equation/scrapers/mlb"
SCRAPER_MODULES=(
    mlb_game_scraper.py
    mlb_pitcher_scraper.py
    mlb_weather_scraper.py
    mlb_lineup_scraper.py
)

echo "Syncing ${#EXPORTER_MODULES[@]} exporter modules from edge-equation-scrapers@${REF}"
for m in "${EXPORTER_MODULES[@]}"; do
    echo "  exporters/mlb/${m}"
    curl -fsSL --max-time 30 "${BASE}/${m}" -o "${DEST}/${m}"
done

if [[ -d "$SCRAPER_DEST" ]]; then
    echo "Syncing ${#SCRAPER_MODULES[@]} scraper modules"
    for m in "${SCRAPER_MODULES[@]}"; do
        echo "  scrapers/mlb/${m}"
        curl -fsSL --max-time 30 "${SCRAPER_BASE}/${m}" -o "${SCRAPER_DEST}/${m}"
    done
fi

echo
echo "Sync complete. Now re-apply import-path patches — see"
echo "docs/MLB_PIPELINE_CUTOVER.md \"Stub-replacement script\" section"
echo "for the exact edits."
