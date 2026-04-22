"""
CLI entry point.

Subcommands:
  ledger     Run The Ledger card (9am CT) -- season record + model health.
  daily      Run the Daily Edge slate (11am CT).
  spotlight  Run the Spotlight card (4pm CT) -- deep dive on trending game.
  evening    Run the Evening Edge slate (6pm CT) -- posts only on material changes.
  overseas   Run the Overseas Edge slate (11pm CT) -- KBO/NPB/Soccer, no props.
  settle     Record outcomes from a CSV and settle stored picks.
  pipeline   Legacy Phase-1 pipeline demo (kept for backwards compat).

Every free-content publish step is gated by compliance_test(
require_ledger_footer=True); any violation aborts the publish and exits
non-zero -- no post goes out without the mandatory footer + disclaimer.

Invocation (any scheduler that can call Python works here):

  python -m edge_equation ledger --publish
  python -m edge_equation daily --publish
  python -m edge_equation spotlight --publish
  python -m edge_equation evening --publish --leagues MLB,NHL
  python -m edge_equation overseas --publish

Common flags:
  --db PATH        SQLite DB path (default: env EDGE_EQUATION_DB or ./edge_equation.db)
  --dry-run        Don't actually post; return dry-run results. Default ON
                   for safety; pass --publish OR --no-dry-run to go live.
  --publish        Invoke X + Discord + Email publishers (still respects --dry-run)
  --leagues LIST   Comma-separated league codes (MLB,NFL,NHL,NBA,KBO,NPB)
  --csv-dir PATH   Directory containing manual-entry CSVs (default: data/)
  --prefer-mock    Skip the odds API and force the mock source (development)
  --public-mode    Strip edge/kelly and inject disclaimer + Ledger footer (default ON)
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from typing import List, Optional

from edge_equation.compliance import compliance_test
from edge_equation.engine.realization import RealizationTracker
from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    CARD_TYPE_EVENING,
    CARD_TYPE_LEDGER,
    CARD_TYPE_OVERSEAS_EDGE,
    CARD_TYPE_SPOTLIGHT,
    DEFAULT_LEAGUES,
    OVERSEAS_LEAGUES,
    ScheduledRunner,
    load_prior_daily_edge_picks,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.realization_store import RealizationStore
from edge_equation.posting.ledger import LedgerStore
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.publishing.x_formatter import format_card as format_x_text
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


def _compliance_gate(card: dict, card_type: str) -> Optional[int]:
    """
    Pre-publish compliance check. Returns None on pass, an exit code on
    fail (and prints the violations). Free-content cards must carry the
    Season Ledger footer + disclaimer per Phase 20.
    """
    report = compliance_test(card, require_ledger_footer=True)
    if report.ok:
        return None
    print(json.dumps({
        "compliance_gate": "blocked",
        "card_type": card_type,
        "violations": report.violations,
    }, indent=2), file=sys.stderr)
    return 3


def _default_leagues_for(card_type: str, explicit: Optional[str]) -> List[str]:
    if explicit:
        return _parse_leagues(explicit)
    if card_type == CARD_TYPE_OVERSEAS_EDGE:
        return list(OVERSEAS_LEAGUES)
    return list(DEFAULT_LEAGUES)


def _run_slate(args: argparse.Namespace, card_type: str) -> int:
    conn = _open_db(args.db)
    public_mode = getattr(args, "public_mode", True)
    try:
        run_dt = datetime.utcnow()
        leagues = _default_leagues_for(card_type, args.leagues)
        ledger_stats = None
        if card_type == CARD_TYPE_LEDGER:
            # The Ledger post is purely season-record + model-health; it
            # doesn't consult any slate. Compute from already-settled picks.
            ledger_stats = LedgerStore.compute(conn)
        elif public_mode:
            # Every free-content card must carry the Season Ledger footer,
            # so compute it up front and flow it into build_card.
            ledger_stats = LedgerStore.compute(conn)

        # Compliance gate: build the card once in-process (independent of
        # any idempotency short-circuit inside ScheduledRunner) so we can
        # block publishes that would fail the compliance rules.
        if args.publish and public_mode:
            preview_card = PostingFormatter.build_card(
                card_type=card_type,
                picks=[],
                generated_at=run_dt.isoformat(),
                public_mode=True,
                ledger_stats=ledger_stats,
                skip_filter=True,
            )
            blocked = _compliance_gate(preview_card, card_type)
            if blocked is not None:
                return blocked

        summary = ScheduledRunner.run(
            card_type=card_type,
            conn=conn,
            run_datetime=run_dt,
            leagues=leagues,
            publish=args.publish,
            dry_run=args.dry_run,
            csv_dir=args.csv_dir,
            prefer_mock=args.prefer_mock,
            public_mode=public_mode,
            ledger_stats=ledger_stats,
        )

        preview_dir = getattr(args, "preview_dir", None)
        if preview_dir:
            from pathlib import Path
            picks_records = PickStore.list_by_slate(conn, summary.slate_id)
            built_picks = [r.to_pick() for r in picks_records]
            resolved_prior = None
            if card_type == CARD_TYPE_EVENING:
                resolved_prior = load_prior_daily_edge_picks(conn, before=run_dt)
            preview_card = PostingFormatter.build_card(
                card_type=card_type,
                picks=built_picks,
                generated_at=run_dt.isoformat(),
                public_mode=public_mode,
                ledger_stats=ledger_stats,
                prior_picks=resolved_prior,
            )
            preview_text = format_x_text(preview_card)
            out_dir = Path(preview_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{card_type}.txt").write_text(preview_text, encoding="utf-8")
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


def _cmd_ledger(args: argparse.Namespace) -> int:
    return _run_slate(args, CARD_TYPE_LEDGER)


def _cmd_spotlight(args: argparse.Namespace) -> int:
    return _run_slate(args, CARD_TYPE_SPOTLIGHT)


def _cmd_overseas(args: argparse.Namespace) -> int:
    return _run_slate(args, CARD_TYPE_OVERSEAS_EDGE)


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


def _cmd_load_results(args: argparse.Namespace) -> int:
    from edge_equation.stats.csv_loader import ResultsCsvLoader
    from edge_equation.stats.results import GameResultsStore
    conn = _open_db(args.db)
    try:
        ids = ResultsCsvLoader.load_file(conn, args.results_csv)
        counts = {}
        for league in ("MLB", "NFL", "NHL", "NBA", "KBO", "NPB", "SOC"):
            counts[league] = GameResultsStore.count_by_league(conn, league)
    finally:
        conn.close()
    print(json.dumps({
        "rows_loaded": len(ids),
        "totals_by_league": counts,
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
    public = p.add_mutually_exclusive_group()
    public.add_argument("--public-mode", dest="public_mode", action="store_true", default=True,
                        help="Strip edge/kelly and inject disclaimer + Ledger footer (default ON)")
    public.add_argument("--no-public-mode", dest="public_mode", action="store_false",
                        help="Disable public-mode sanitization (premium / internal use only)")
    p.add_argument("--preview-dir", type=str, default=None,
                   help="Directory to write the rendered X text for the built card. "
                        "Use with --no-publish for offline review of what would post.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edge-equation")
    sub = parser.add_subparsers(dest="subcommand", required=False)

    p_ledger = sub.add_parser("ledger", help="Run The Ledger card (9am CT)")
    _add_slate_flags(p_ledger)
    p_ledger.set_defaults(func=_cmd_ledger)

    p_daily = sub.add_parser("daily", help="Run the Daily Edge slate (11am CT)")
    _add_slate_flags(p_daily)
    p_daily.set_defaults(func=_cmd_daily)

    p_spot = sub.add_parser("spotlight", help="Run the Spotlight card (4pm CT)")
    _add_slate_flags(p_spot)
    p_spot.set_defaults(func=_cmd_spotlight)

    p_even = sub.add_parser("evening", help="Run the Evening Edge slate (6pm CT)")
    _add_slate_flags(p_even)
    p_even.set_defaults(func=_cmd_evening)

    p_over = sub.add_parser("overseas", help="Run the Overseas Edge slate (11pm CT)")
    _add_slate_flags(p_over)
    p_over.set_defaults(func=_cmd_overseas)

    p_settle = sub.add_parser("settle", help="Record outcomes and settle picks")
    p_settle.add_argument("outcomes_csv")
    p_settle.add_argument("--db", type=str, default=None)
    p_settle.add_argument("--slate-id", type=str, default=None)
    p_settle.set_defaults(func=_cmd_settle)

    p_load = sub.add_parser(
        "load-results",
        help="Load completed-game scores from a CSV (for stats / Elo replay)",
    )
    p_load.add_argument("results_csv")
    p_load.add_argument("--db", type=str, default=None)
    p_load.set_defaults(func=_cmd_load_results)

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
