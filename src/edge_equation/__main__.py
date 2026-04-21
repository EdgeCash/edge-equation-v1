"""
CLI entry point.

Subcommands:
  daily      Run the daily-edge slate (ingest -> engine -> persist -> publish).
  evening    Run the evening-edge slate.
  settle     Record outcomes from a CSV and settle stored picks.
  pipeline   Legacy Phase-1 pipeline demo (kept for backwards compat).

Invocation (any scheduler that can call Python works here):

  python -m edge_equation daily --publish
  python -m edge_equation evening --publish --leagues MLB,NHL
  python -m edge_equation settle data/outcomes_2026-04-20.csv
  python -m edge_equation pipeline --mode daily

Common flags:
  --db PATH        SQLite DB path (default: env EDGE_EQUATION_DB or ./edge_equation.db)
  --dry-run        Don't actually post; return dry-run results. Default ON
                   for safety; pass --publish OR --no-dry-run to go live.
  --publish        Invoke X + Discord + Email publishers (still respects --dry-run)
  --leagues LIST   Comma-separated league codes (MLB,NFL,NHL,NBA,KBO,NPB)
  --csv-dir PATH   Directory containing manual-entry CSVs (default: data/)
  --prefer-mock    Skip the odds API and force the mock source (development)
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from typing import List, Optional

from edge_equation.engine.realization import RealizationTracker
from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    CARD_TYPE_EVENING,
    DEFAULT_LEAGUES,
    ScheduledRunner,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.realization_store import RealizationStore
from edge_equation.utils.logging import get_logger


logger = get_logger("edge-equation")


def _parse_leagues(raw: Optional[str]) -> List[str]:
    if not raw:
        return list(DEFAULT_LEAGUES)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _open_db(path: Optional[str]):
    conn = Database.open(path)
    Database.migrate(conn)
    return conn


def _run_slate(args: argparse.Namespace, card_type: str) -> int:
    conn = _open_db(args.db)
    try:
        summary = ScheduledRunner.run(
            card_type=card_type,
            conn=conn,
            run_datetime=datetime.utcnow(),
            leagues=_parse_leagues(args.leagues),
            publish=args.publish,
            dry_run=args.dry_run,
            csv_dir=args.csv_dir,
            prefer_mock=args.prefer_mock,
        )
    finally:
        conn.close()
    print(json.dumps(summary.to_dict(), indent=2, default=str))
    failures = [
        r for r in summary.publish_results
        if hasattr(r, "success") and not r.success and not getattr(r, "failsafe_triggered", False)
    ]
    return 1 if failures else 0


def _cmd_daily(args: argparse.Namespace) -> int:
    return _run_slate(args, CARD_TYPE_DAILY)


def _cmd_evening(args: argparse.Namespace) -> int:
    return _run_slate(args, CARD_TYPE_EVENING)


def _cmd_settle(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    recorded = 0
    try:
        with open(args.outcomes_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            required = {"game_id", "market_type", "selection", "outcome"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                print(f"error: outcomes CSV missing columns: {sorted(missing)}", file=sys.stderr)
                return 2
            for row in reader:
                actual_value = row.get("actual_value") or None
                from decimal import Decimal
                av = Decimal(actual_value) if actual_value else None
                RealizationStore.record_outcome(
                    conn,
                    game_id=row["game_id"].strip(),
                    market_type=row["market_type"].strip(),
                    selection=row["selection"].strip(),
                    outcome=row["outcome"].strip(),
                    actual_value=av,
                )
                recorded += 1
        settled = RealizationTracker.settle_picks(conn, slate_id=args.slate_id)
        hit_rate = RealizationTracker.hit_rate_by_grade(conn)
    finally:
        conn.close()
    print(json.dumps({
        "recorded_outcomes": recorded,
        "matched": settled["matched"],
        "updated": settled["updated"],
        "hit_rate_by_grade": hit_rate,
    }, indent=2, default=str))
    return 0


def _cmd_pipeline(args: argparse.Namespace) -> int:
    # Phase-1 demo pipeline retained for backwards compatibility.
    from edge_equation.engine.modes import PipelineMode
    from edge_equation.engine.pipeline import EnginePipeline
    mode = PipelineMode(args.mode)
    logger.info(f"Running Edge Equation engine in mode: {mode.value}")
    pipeline = EnginePipeline()
    result = pipeline.run()
    logger.info(f"Engine result: {result}")
    print(json.dumps(result, indent=2, default=str))
    return 0


def _add_slate_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", type=str, default=None)
    p.add_argument("--leagues", type=str, default=None,
                   help="Comma-separated, e.g. MLB,NFL,NHL,KBO")
    p.add_argument("--csv-dir", type=str, default=None)
    publish = p.add_mutually_exclusive_group()
    publish.add_argument("--publish", action="store_true", default=False,
                         help="Invoke X, Discord, Email publishers")
    publish.add_argument("--no-publish", dest="publish", action="store_false")
    dry = p.add_mutually_exclusive_group()
    dry.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                     help="Simulate publishers without real network I/O (default ON)")
    dry.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                     help="Actually post -- requires real credentials")
    p.add_argument("--prefer-mock", action="store_true", default=False,
                   help="Force the stubbed ingestion sources (dev/testing)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edge-equation")
    sub = parser.add_subparsers(dest="subcommand", required=False)

    p_daily = sub.add_parser("daily", help="Run the daily-edge slate")
    _add_slate_flags(p_daily)
    p_daily.set_defaults(func=_cmd_daily)

    p_even = sub.add_parser("evening", help="Run the evening-edge slate")
    _add_slate_flags(p_even)
    p_even.set_defaults(func=_cmd_evening)

    p_settle = sub.add_parser("settle", help="Record outcomes and settle picks")
    p_settle.add_argument("outcomes_csv")
    p_settle.add_argument("--db", type=str, default=None)
    p_settle.add_argument("--slate-id", type=str, default=None)
    p_settle.set_defaults(func=_cmd_settle)

    p_pipe = sub.add_parser("pipeline", help="Legacy Phase-1 pipeline demo")
    p_pipe.add_argument("--mode", type=str, default="daily")
    p_pipe.set_defaults(func=_cmd_pipeline)

    # Legacy: no subcommand runs the pipeline demo (preserves old invocation).
    parser.add_argument("--mode", type=str, default="daily",
                        help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        # Legacy back-compat: invoke the pipeline demo.
        return _cmd_pipeline(argparse.Namespace(mode=getattr(args, "mode", "daily")))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
