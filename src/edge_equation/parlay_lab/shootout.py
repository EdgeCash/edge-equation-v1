"""Shootout orchestrator + CLI.

Runs each registered engine over the same backfill window, scores
the recommended parlays, and writes a markdown leaderboard. Re-run
any time --- output is one timestamped report per invocation.

Usage::

    # Smallest sane run --- last 30 days, default engines
    python -m edge_equation.parlay_lab.shootout --window-days 30

    # Pick specific engines + a custom date window
    python -m edge_equation.parlay_lab.shootout \\
        --engines baseline,deduped --after 2025-08-01 --before 2025-10-01

    # Limit to MLB game-results markets only (skip first_inning / first_5)
    python -m edge_equation.parlay_lab.shootout \\
        --bet-types moneyline,run_line,totals,team_totals
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from edge_equation.engines.parlay.config import ParlayConfig
from edge_equation.engines.tiering import Tier

from .backfill import iter_slates, load_backfill
from .backfill_props import PropSyntheticConfig, load_props_backfill
from .engines import ENGINES, all_engines, resolve
from .metrics import EngineScore, score_engine
from .report import ShootoutReport, write_report


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BACKFILL = (
    _REPO_ROOT / "website" / "public" / "data" / "mlb" / "backtest.json"
)
_DEFAULT_PROPS_DIR = _REPO_ROOT / "data" / "prizepicks" / "snapshots"
_DEFAULT_REPORT_DIR = _REPO_ROOT / "src" / "edge_equation" / "parlay_lab" / "reports"


def _parse_engines(arg: Optional[str]):
    if not arg:
        return all_engines()
    out = []
    for name in (n.strip() for n in arg.split(",") if n.strip()):
        if name not in ENGINES:
            raise SystemExit(
                f"Unknown engine: {name!r}. "
                f"Registered: {', '.join(sorted(ENGINES))}",
            )
        out.append(resolve(name))
    return out


def _parse_bet_types(arg: Optional[str]) -> Optional[set[str]]:
    if not arg:
        return None
    return {n.strip() for n in arg.split(",") if n.strip()}


def _resolve_window(
    after: Optional[str], before: Optional[str], window_days: Optional[int],
    fallback_first: str, fallback_last: str,
) -> tuple[Optional[str], Optional[str]]:
    """Reconcile --after / --before / --window-days into an inclusive
    [after, before] pair against the backfill's actual date range.

    --window-days takes the most recent N days from the backfill.
    Explicit --after / --before override individual ends.
    """
    if window_days is not None and after is None and before is None:
        if not fallback_last:
            return None, None
        last_dt = datetime.fromisoformat(fallback_last)
        after_dt = last_dt - timedelta(days=window_days - 1)
        return after_dt.date().isoformat(), fallback_last
    return after, before


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Parlay-engine shootout.")
    parser.add_argument(
        "--source", default="mlb", choices=("mlb", "props"),
        help="Backfill source. ``mlb`` reads MLB game-results from "
              "backtest.json (real outcomes). ``props`` reads PrizePicks "
              "snapshots + synthesizes outcomes via the parametric model "
              "in backfill_props.py.",
    )
    parser.add_argument(
        "--bets-path", default=str(_DEFAULT_BACKFILL),
        help="Path to backtest.json (--source mlb). Default: MLB engine output.",
    )
    parser.add_argument(
        "--snapshots-dir", default=str(_DEFAULT_PROPS_DIR),
        help="Directory of PrizePicks snapshots (--source props). Default: "
              "data/prizepicks/snapshots/.",
    )
    parser.add_argument(
        "--engines", default=None,
        help="Comma-separated engine names. Default: every registered engine.",
    )
    parser.add_argument(
        "--after", default=None,
        help="Inclusive ISO date lower bound (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--before", default=None,
        help="Inclusive ISO date upper bound (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--window-days", type=int, default=None,
        help="Most recent N days. Ignored when --after/--before set.",
    )
    parser.add_argument(
        "--bet-types", default=None,
        help="Comma-separated bet_types to keep (e.g. moneyline,totals).",
    )
    parser.add_argument(
        "--min-tier", default="LEAN",
        choices=[t.value for t in Tier if t != Tier.NO_PLAY],
        help="Drop legs below this tier before each engine sees them.",
    )
    parser.add_argument(
        "--max-legs", type=int, default=4,
        help="ParlayConfig.max_legs override. Default 4 (a 4-leg cap "
              "trades off coverage vs MC cost on long backfills).",
    )
    parser.add_argument(
        "--mc-trials", type=int, default=2_000,
        help="ParlayConfig.mc_trials override. Default 2000 (lower than "
              "production's 10k --- the shootout is comparing engines, "
              "not pricing tickets to the cent).",
    )
    parser.add_argument(
        "--max-pool-size", type=int, default=20,
        help="ParlayConfig.max_pool_size override. Default 20.",
    )
    parser.add_argument(
        "--min-joint-prob", type=float, default=None,
        help="ParlayConfig.min_joint_prob override. Default 0.40 for "
              "--source mlb and 0.20 for --source props (props legs price "
              "at higher decimal odds per the bucket fair-odds model, "
              "which makes joint probs structurally lower; production "
              "is 0.68).",
    )
    parser.add_argument(
        "--min-ev-units", type=float, default=0.05,
        help="ParlayConfig.min_ev_units override. Default 0.05 for the "
              "shootout (production is 0.25). Same reasoning.",
    )
    parser.add_argument(
        "--out-dir", default=str(_DEFAULT_REPORT_DIR),
        help="Directory for the rendered markdown report.",
    )
    args = parser.parse_args(argv)

    engines = _parse_engines(args.engines)
    bet_types = _parse_bet_types(args.bet_types)
    min_tier = Tier(args.min_tier)

    # Pre-load the backfill once, then re-iterate for each engine.
    if args.source == "props":
        snapshots_dir = Path(args.snapshots_dir)
        if not snapshots_dir.exists():
            print(f"snapshots dir not found: {snapshots_dir}", file=sys.stderr)
            return 1
        print(f"Loading PROPS backfill from {snapshots_dir}...")
        source, slates = load_props_backfill(
            snapshots_dir,
            min_tier=min_tier,
            config=PropSyntheticConfig(),
        )
    else:
        bets_path = Path(args.bets_path)
        if not bets_path.exists():
            print(f"backfill not found: {bets_path}", file=sys.stderr)
            return 1
        print(f"Loading MLB backfill from {bets_path}...")
        source, slates = load_backfill(
            bets_path, bet_types=bet_types, min_tier=min_tier,
        )
    after, before = _resolve_window(
        args.after, args.before, args.window_days,
        source.first_date, source.last_date,
    )
    if after or before:
        slates = [
            s for s in slates
            if (not after or s.date >= after)
            and (not before or s.date <= before)
        ]
        # Refresh the source provenance to reflect the actual window run.
        source = source.__class__(
            path=source.path,
            n_rows=sum(len(s.graded_legs) for s in slates),
            first_date=slates[0].date if slates else "",
            last_date=slates[-1].date if slates else "",
        )
    slates = list(iter_slates(slates, min_legs_per_slate=2))
    print(
        f"  -> {len(slates)} slates, {source.first_date} -> {source.last_date}, "
        f"{source.n_rows} qualifying legs total",
    )

    # Source-aware default: props legs are fair-priced at higher decimal
    # odds than the -110 MLB game-results, so joint-prob floors that work
    # for MLB are too tight for props. Operator can override via the CLI.
    min_joint_prob = args.min_joint_prob
    if min_joint_prob is None:
        min_joint_prob = 0.20 if args.source == "props" else 0.40

    config = ParlayConfig(
        min_tier=min_tier,
        max_legs=args.max_legs,
        mc_trials=args.mc_trials,
        max_pool_size=args.max_pool_size,
        min_joint_prob=min_joint_prob,
        min_ev_units=args.min_ev_units,
    )

    scores: list[EngineScore] = []
    for engine in engines:
        print(f"  Running engine: {engine.name}")
        per_slate = []
        for slate in slates:
            cands = engine.build(slate.legs, config)
            per_slate.append((slate, cands))
        score = score_engine(engine.name, per_slate)
        scores.append(score)
        roi = score.roi_pct
        print(
            f"    -> {score.n_parlays} parlays, "
            f"{score.n_wins}-{score.n_losses}-{score.n_pushes}, "
            f"ROI {roi:+.2f}%",
        )

    report = ShootoutReport(source=source, scores=scores)
    out_path = write_report(report, out_dir=args.out_dir)
    print(f"\nReport written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
