"""CLI entry: ``python -m edge_equation.exporters.closing_lines``.

Snaps the current Odds API payload for one or more sports and appends
flat rows to ``data/closing_lines/<sport>/<season>.jsonl``.

Usage::

    # Snap every sport (uses ODDS_API_KEY env var):
    python -m edge_equation.exporters.closing_lines --sports mlb wnba nba nhl

    # One sport, single shot:
    python -m edge_equation.exporters.closing_lines --sports mlb

    # Dry-run -- print what we'd write but don't append:
    python -m edge_equation.exporters.closing_lines --sports mlb --dry-run

The cron / GitHub workflow is the intended caller.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from edge_equation.exporters.closing_lines.snapshot import (
    DEFAULT_OUTPUT_DIR, SPORT_KEYS, fetch_odds_payload,
    normalize_payload, append_snapshot, snapshot,
)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edge_equation.exporters.closing_lines",
        description="Snap current Odds API prices and append to JSONL log.",
    )
    parser.add_argument(
        "--sports", nargs="+", default=["mlb"],
        choices=list(SPORT_KEYS.keys()),
        help="One or more sports to snap.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--regions", default="us",
        help="Odds API regions (default 'us').",
    )
    parser.add_argument(
        "--markets", default="h2h,spreads,totals",
        help="Odds API markets (default h2h,spreads,totals).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what we'd write without appending.",
    )
    args = parser.parse_args(argv)

    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"closing_lines: captured_at={captured_at}")
    print(f"  sports={args.sports}  regions={args.regions}  markets={args.markets}")

    rc = 0
    for sport in args.sports:
        if args.dry_run:
            try:
                payload = fetch_odds_payload(
                    sport, regions=args.regions, markets=args.markets,
                )
            except Exception as e:
                print(f"  [{sport}] fetch error ({type(e).__name__}): {e}")
                rc = 1
                continue
            rows = normalize_payload(
                payload, sport=sport, captured_at=captured_at,
            )
            print(
                f"  [{sport}] dry-run: {len(payload)} event(s), "
                f"{len(rows)} row(s) (would write)"
            )
            continue

        result = snapshot(
            sport,
            output_dir=args.output_dir,
            captured_at=captured_at,
            regions=args.regions,
            markets=args.markets,
        )
        if result.error:
            print(f"  [{sport}] error: {result.error}")
            rc = 1
            continue
        print(
            f"  [{sport}] events={result.n_events} books={result.n_books} "
            f"rows={result.n_rows}"
        )
        for path, n in result.files.items():
            print(f"      +{n:>5} -> {path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
