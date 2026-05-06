#!/usr/bin/env python3
"""Unified MLB daily command — one entry point for the full card.

This is the single command operators (and the GitHub Actions
``mlb-daily`` workflow) run to produce the full MLB output for a
given date::

    # Full daily output: all markets + both parlay engines + website feed.
    python run_daily_mlb.py --all

    # Just print the unified card to stdout (no website export).
    python run_daily_mlb.py

    # Specific date.
    python run_daily_mlb.py --all --date 2026-05-06

The ``--all`` flag is the contract the audit calls out: it produces
NRFI/YRFI, full-game, player props, and both new parlay sections
(``mlb_game_results_parlay`` / ``mlb_player_props_parlay``) AND
writes the website daily feed (``website/public/data/daily/latest.json``)
that the EdgeEquation.com daily-edge page consumes.

The unified runner treats every per-market engine as best-effort —
if e.g. lineups or odds aren't available yet for one market, the
others still publish, and the missing market shows up in the output
with a ``Limited Data`` flag. The site is therefore always live
before first pitch even when one upstream feed is delayed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as _date
from pathlib import Path
from typing import Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# Default cache paths — match the per-engine config defaults so
# everything reads/writes from the same DuckDBs the per-engine CLIs
# already use.
DEFAULT_NRFI_DB = REPO_ROOT / "data" / "nrfi_cache" / "nrfi.duckdb"
DEFAULT_PROPS_DB = REPO_ROOT / "data" / "props_cache" / "props.duckdb"
DEFAULT_FULLGAME_DB = REPO_ROOT / "data" / "fullgame_cache" / "fullgame.duckdb"
DEFAULT_FEED_OUT = REPO_ROOT / "website" / "public" / "data" / "daily" / "latest.json"


def _print_banner(target_date: str, *, run_all: bool) -> None:
    print("=" * 60)
    print(f"MLB DAILY CARD  ·  {target_date}")
    print(
        "=" * 60
        + "\n"
        + ("Mode: --all (unified card + website feed)" if run_all
              else "Mode: card preview (no website export)")
    )
    print()


def _build_card(target_date: Optional[str], *, include_alternates: bool):
    """Run the unified MLB engine. Imported lazily so missing optional
    deps (numpy / duckdb / pybaseball) only hit the call sites that
    actually need them."""
    from edge_equation.engines.mlb.run_daily import build_unified_mlb_card
    return build_unified_mlb_card(
        target_date,
        include_alternates=include_alternates,
    )


def _format_card(card) -> str:
    from edge_equation.engines.mlb.run_daily import _format_card
    return _format_card(card)


def _export_feed(target_date: str) -> Optional[Path]:
    """Run the website daily-feed exporter so EdgeEquation.com gets
    today's full card (single picks + both parlay sections)."""
    try:
        from edge_equation.engines.website.build_daily_feed import (
            build_bundle, write_bundle,
        )
        from edge_equation.engines.nrfi.data.storage import NRFIStore
    except ImportError as exc:
        print(
            f"[run_daily_mlb] feed export skipped — {exc}. "
            "(Install [.nrfi] extras to enable.)",
            file=sys.stderr,
        )
        return None

    nrfi_path = DEFAULT_NRFI_DB
    if not nrfi_path.exists():
        print(
            f"[run_daily_mlb] feed export skipped — NRFI DB missing at "
            f"{nrfi_path}.",
            file=sys.stderr,
        )
        return None

    store = NRFIStore(str(nrfi_path))
    props_store = None
    fullgame_store = None
    try:
        if DEFAULT_PROPS_DB.exists():
            from edge_equation.engines.props_prizepicks.data.storage import (
                PropsStore,
            )
            props_store = PropsStore(str(DEFAULT_PROPS_DB))
        if DEFAULT_FULLGAME_DB.exists():
            from edge_equation.engines.full_game.data.storage import FullGameStore
            fullgame_store = FullGameStore(str(DEFAULT_FULLGAME_DB))

        bundle = build_bundle(
            store, target_date,
            props_store=props_store,
            fullgame_store=fullgame_store,
        )
        write_bundle(bundle, DEFAULT_FEED_OUT)
        return DEFAULT_FEED_OUT
    finally:
        store.close()
        if props_store is not None:
            props_store.close()
        if fullgame_store is not None:
            fullgame_store.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the unified MLB daily card (all markets + both parlay "
            "engines). With --all, also writes the website daily feed."
        ),
    )
    parser.add_argument(
        "--all", dest="run_all", action="store_true",
        help="Run the full pipeline: card + website feed export.",
    )
    parser.add_argument(
        "--date", default=None,
        help="Slate date YYYY-MM-DD. Default: today (UTC).",
    )
    parser.add_argument(
        "--include-alternates", action="store_true",
        help="Pull alternate Run_Line / Total / Team_Total markets via "
              "the per-event Odds API endpoint (extra credits).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    target = args.date or _date.today().isoformat()
    _print_banner(target, run_all=args.run_all)

    card = _build_card(target, include_alternates=args.include_alternates)
    print(_format_card(card))

    if args.run_all:
        out_path = _export_feed(target)
        if out_path is not None:
            print(f"[run_daily_mlb] daily feed written → {out_path}")
        else:
            print(
                "[run_daily_mlb] daily feed not written — see warnings above."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
