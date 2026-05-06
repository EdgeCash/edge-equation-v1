#!/usr/bin/env python3
"""Unified NCAAF daily command — one entry point for the full card.

Mirrors `run_daily_nfl.py`. Single command::

    python run_daily_ncaaf.py --all
    python run_daily_ncaaf.py            # card preview, no website export
    python run_daily_ncaaf.py --all --date 2026-09-05
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
    print(f"NCAAF DAILY CARD  ·  {target_date}")
    print(
        "=" * 60
        + "\n"
        + ("Mode: --all (unified card + website feed)" if run_all
              else "Mode: card preview (no website export)")
    )
    print()


def _build_card(target_date: Optional[str]):
    from edge_equation.engines.ncaaf.parlay_runner import (
        build_unified_ncaaf_card,
    )
    return build_unified_ncaaf_card(target_date)


def _format_card(card) -> str:
    from edge_equation.engines.ncaaf.parlay_runner import _format_card
    return _format_card(card)


def _export_feed(target_date: str) -> Optional[Path]:
    try:
        from edge_equation.engines.website.build_daily_feed import (
            build_bundle, write_bundle,
        )
    except ImportError as exc:
        print(
            f"[run_daily_ncaaf] feed export skipped — {exc}.",
            file=sys.stderr,
        )
        return None

    bundle = build_bundle(
        store=None, target_date=target_date,
        props_store=None, fullgame_store=None,
        include_ncaaf=True,
    )
    write_bundle(bundle, DEFAULT_FEED_OUT)
    return DEFAULT_FEED_OUT


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the unified NCAAF daily card. With --all, also writes "
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
            print(f"[run_daily_ncaaf] daily feed written → {out_path}")
        else:
            print(
                "[run_daily_ncaaf] daily feed not written — see warnings above."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
