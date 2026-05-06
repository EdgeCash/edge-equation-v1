#!/usr/bin/env python3
"""Overnight backfill orchestrator across all four pro leagues.

Single entry point the operator runs at end of day. Delegates each
league's pull to its dedicated script and runs them sequentially so
the combined RPS stays polite even if multiple leagues share an
upstream (ESPN powers WNBA + NBA + NHL pulls).

Sequencing matters
~~~~~~~~~~~~~~~~~~
MLB props goes first because it's the longest job (~32h for full
2024+2025). Game-results pulls (NBA / NHL / WNBA player stats) all
finish in <1h, so kicking them off after MLB props ensures none of
them block the long-running pull. The orchestrator runs them in
sub-process so a crash in one doesn't stop the others.

Resumability
~~~~~~~~~~~~
Every individual ingester is resumable. A second invocation of this
script just picks up where each league left off. Safe to re-run any
time -- already-fetched IDs are skipped.

Usage::

    # Kick off all leagues for sane default seasons:
    python scripts/harvest_overnight.py

    # Only specific leagues:
    python scripts/harvest_overnight.py --leagues nba nhl

    # Override seasons (default depends on the league):
    python scripts/harvest_overnight.py --leagues mlb-props \\
        --mlb-seasons 2023 2024 2025

    # Smoke test with --limit (forwarded to each league):
    python scripts/harvest_overnight.py --leagues nba --limit 5
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence


SCRIPTS_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable


# Sane default seasons -- recent enough that lineup constructions match
# today's roster while being deep enough to clear the 200+ bet gate.
DEFAULT_SEASONS = {
    "mlb-props": [2024, 2025],
    "nba":       [2024, 2025],
    "nhl":       [2024, 2025],
    "wnba-players": [2022, 2023, 2024, 2025],
}

LEAGUE_SCRIPTS = {
    "mlb-props":    "backfill_player_games.py",
    "nba":          "backfill_nba_games.py",
    "nhl":          "backfill_nhl_games.py",
    "wnba-players": "backfill_wnba_player_games.py",
}


def run_league(
    league: str, seasons: Sequence[int], rps: Optional[float],
    limit: Optional[int],
) -> int:
    """Run one league's backfill in a subprocess. Returns the exit code.
    Streams stdout/stderr live so the operator can watch progress."""
    script = SCRIPTS_DIR / LEAGUE_SCRIPTS[league]
    if not script.exists():
        print(f"[!] script missing for {league}: {script}")
        return 1
    cmd: List[str] = [
        PYTHON, str(script), "--seasons", *[str(s) for s in seasons],
    ]
    if rps is not None:
        cmd.extend(["--rps", str(rps)])
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    print(f"\n=== [{league}] {' '.join(shlex.quote(c) for c in cmd)} ===")
    started = time.time()
    proc = subprocess.run(cmd)
    elapsed = time.time() - started
    print(
        f"=== [{league}] exit {proc.returncode} after {elapsed:.0f}s ==="
    )
    return proc.returncode


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harvest_overnight",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--leagues", nargs="+",
        choices=list(LEAGUE_SCRIPTS.keys()) + ["all"],
        default=["all"],
        help="Leagues to harvest. 'all' runs everything in safe order.",
    )
    parser.add_argument(
        "--mlb-seasons", type=int, nargs="+", default=None,
        help="Override MLB-props seasons.",
    )
    parser.add_argument(
        "--nba-seasons", type=int, nargs="+", default=None,
    )
    parser.add_argument(
        "--nhl-seasons", type=int, nargs="+", default=None,
    )
    parser.add_argument(
        "--wnba-seasons", type=int, nargs="+", default=None,
    )
    parser.add_argument(
        "--rps", type=float, default=None,
        help="Override RPS for all leagues (each script's default if unset).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Per-league cap (handy for smoke tests).",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true",
        help="Don't abort the chain when one league's pull exits non-zero.",
    )
    args = parser.parse_args(argv)

    if "all" in args.leagues:
        # MLB props first (longest); then the lighter pulls.
        ordered = ["mlb-props", "wnba-players", "nba", "nhl"]
    else:
        ordered = args.leagues

    season_overrides = {
        "mlb-props":    args.mlb_seasons,
        "nba":          args.nba_seasons,
        "nhl":          args.nhl_seasons,
        "wnba-players": args.wnba_seasons,
    }

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[harvest_overnight] start {started}")
    print(f"  leagues={ordered}")
    print(f"  default seasons: {DEFAULT_SEASONS}")
    if args.rps is not None:
        print(f"  RPS override: {args.rps}")
    if args.limit is not None:
        print(f"  per-league limit: {args.limit}")

    failures: List[str] = []
    overall_started = time.time()
    for league in ordered:
        seasons = season_overrides.get(league) or DEFAULT_SEASONS[league]
        rc = run_league(league, seasons, args.rps, args.limit)
        if rc != 0:
            failures.append(f"{league}(exit={rc})")
            if not args.continue_on_error:
                print(
                    "[harvest_overnight] aborting -- pass "
                    "--continue-on-error to keep going"
                )
                break

    elapsed = time.time() - overall_started
    print()
    print(f"[harvest_overnight] done in {elapsed:.0f}s")
    if failures:
        print(f"  failures: {failures}")
        return 1
    print("  all leagues OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
