#!/usr/bin/env bash
# Bootstrap MLB backfill data into data/backfill/mlb/<season>/.
#
# What this populates:
#   data/backfill/mlb/<season>/statcast_xstats.json   ← the file the
#       SplitsLoader checks for prior-season xwOBA. Missing this file
#       is what produced "0/24 probable SPs got prior-season xwOBA
#       data" in dry run #2.
#
# Usage:
#   ./scripts/bootstrap_mlb_backfill.sh                # default season = current_year - 1
#   ./scripts/bootstrap_mlb_backfill.sh 2025           # specific prior season
#   ./scripts/bootstrap_mlb_backfill.sh 2024 2025      # multiple seasons
#
# Verify after running:
#   python -c "
#   from pathlib import Path
#   from edge_equation.exporters.mlb.splits_loader import SplitsLoader
#   sl = SplitsLoader(Path('data/backfill/mlb'))
#   print('2025 xstats:', sl._load_season_xstats(2025) is not None)
#   "
#
# This script is intentionally a thin curl-based bootstrap. The richer
# backfill (full statcast pulls, splits.json, people.json) is what
# scrapers' run_mlb_statcast_backfill.py / run_mlb_people_backfill.py
# / run_mlb_splits_backfill.py do; once we cut over completely we can
# port those entry points. For now this single file unblocks xwOBA.

set -euo pipefail

DEFAULT_SEASON="$(date -u +%Y)"
DEFAULT_SEASON=$((DEFAULT_SEASON - 1))

SEASONS=("$@")
if [[ ${#SEASONS[@]} -eq 0 ]]; then
    SEASONS=("$DEFAULT_SEASON")
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$REPO_ROOT/data/backfill/mlb"
mkdir -p "$ROOT"

# Baseball Savant expected-stats leaderboard endpoint. The leaderboard
# returns CSV with one row per player + the xwoba/xba/xslg/PA fields the
# splits_loader expects. We pull pitching + batting separately, parse
# them, and combine into the {pitching: {pid: {...}}, batting: {...}}
# shape splits_loader._prior_xstats_player() reads.
BASE_URL="https://baseballsavant.mlb.com/leaderboard/expected_statistics"

for SEASON in "${SEASONS[@]}"; do
    echo "Bootstrapping data/backfill/mlb/${SEASON}/statcast_xstats.json"
    OUT_DIR="$ROOT/$SEASON"
    mkdir -p "$OUT_DIR"
    PITCHING_CSV="$OUT_DIR/_xstats_pitching_${SEASON}.csv"
    BATTING_CSV="$OUT_DIR/_xstats_batting_${SEASON}.csv"

    PITCHING_URL="${BASE_URL}?type=pitcher&year=${SEASON}&position=&team=&min=q&csv=true"
    BATTING_URL="${BASE_URL}?type=batter&year=${SEASON}&position=&team=&min=q&csv=true"

    echo "  pitchers..."
    curl -fsSL --max-time 60 "$PITCHING_URL" -o "$PITCHING_CSV"
    echo "  batters..."
    curl -fsSL --max-time 60 "$BATTING_URL" -o "$BATTING_CSV"

    python3 -c "
import csv, json, sys
from pathlib import Path

out_path = Path('$OUT_DIR/statcast_xstats.json')
season = int('$SEASON')

def normalize_id_field(row, *keys):
    for k in keys:
        v = row.get(k) or row.get(k.lower()) or row.get(k.upper())
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return None

def normalize_float(row, *keys):
    for k in keys:
        v = row.get(k) or row.get(k.lower()) or row.get(k.upper())
        if v not in (None, ''):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None

def normalize_int(row, *keys):
    for k in keys:
        v = row.get(k) or row.get(k.lower()) or row.get(k.upper())
        if v not in (None, ''):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                pass
    return 0

def load_csv(path, group):
    rows = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = normalize_id_field(row, 'player_id', 'mlbam_id', 'id')
            if pid is None:
                continue
            rows[str(pid)] = {
                'pa': normalize_int(row, 'pa', 'PA'),
                'xwoba': normalize_float(row, 'est_woba', 'xwoba', 'xwOBA'),
                'xba': normalize_float(row, 'est_ba', 'xba', 'xBA'),
                'xslg': normalize_float(row, 'est_slg', 'xslg', 'xSLG'),
            }
    return rows

payload = {
    'season': season,
    'pitching': load_csv('$PITCHING_CSV', 'pitching'),
    'batting':  load_csv('$BATTING_CSV',  'batting'),
}
out_path.write_text(json.dumps(payload, indent=2))
print(f'  wrote {out_path}: pitchers={len(payload[\"pitching\"])}, batters={len(payload[\"batting\"])}')
"

    rm -f "$PITCHING_CSV" "$BATTING_CSV"
done

echo
echo "Done. Verify with:"
echo "  PYTHONPATH=src python -c \"from pathlib import Path; from edge_equation.exporters.mlb.splits_loader import SplitsLoader; sl = SplitsLoader(Path('data/backfill/mlb')); [print(f'{s}: xstats={sl._load_season_xstats(s) is not None}') for s in (${SEASONS[@]})]\""
