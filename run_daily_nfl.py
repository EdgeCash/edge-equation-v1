#!/usr/bin/env python3
"""Unified NFL daily command — one entry point for the full card.

Mirrors `run_daily_wnba.py` and `run_daily_mlb.py`. Single command::

    python run_daily_nfl.py --all
    python run_daily_nfl.py            # card preview, no website export
    python run_daily_nfl.py --all --date 2026-09-04

`--all` produces the NFL per-row outputs plus both new parlay sections
(`nfl_game_results_parlay` / `nfl_player_props_parlay`) AND writes the
website daily feed for the NFL section.
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


DEFAULT_FEED_OUT = REPO_ROOT / "website" / "public" / "data" / "daily" / "latest.json"


def _print_banner(target_date: str, *, run_all: bool) -> None:
    print("=" * 60)
    print(f"NFL DAILY CARD  ·  {target_date}")
    print(
        "=" * 60
        + "\n"
        + ("Mode: --all (unified card + website feed)" if run_all
              else "Mode: card preview (no website export)")
    )
    print()


def _build_card(target_date: Optional[str]):
    from edge_equation.engines.nfl.parlay_runner import (
        build_unified_nfl_card,
    )
    return build_unified_nfl_card(target_date)


def _format_card(card) -> str:
    from edge_equation.engines.nfl.parlay_runner import _format_card
    return _format_card(card)


def _export_feed(target_date: str) -> Optional[Path]:
    """Run the website daily-feed exporter so EdgeEquation.com gets
    today's full NFL card (per-row + both parlay sections)."""
    try:
        from edge_equation.engines.website.build_daily_feed import (
            build_bundle, write_bundle,
        )
    except ImportError as exc:
        print(
            f"[run_daily_nfl] feed export skipped — {exc}.",
            file=sys.stderr,
        )
        return None

    bundle = build_bundle(
        store=None, target_date=target_date,
        props_store=None, fullgame_store=None,
        include_nfl=True,
    )
    write_bundle(bundle, DEFAULT_FEED_OUT)
    return DEFAULT_FEED_OUT


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the unified NFL daily card. With --all, also writes "
            "the website daily feed."
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
    args = parser.parse_args(list(argv) if argv is not None else None)

    target = args.date or _date.today().isoformat()
    _print_banner(target, run_all=args.run_all)

    card = _build_card(target)
    print(_format_card(card))

    if args.run_all:
        out_path = _export_feed(target)
        if out_path is not None:
            print(f"[run_daily_nfl] daily feed written → {out_path}")
        else:
            print(
                "[run_daily_nfl] daily feed not written — see warnings above."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
