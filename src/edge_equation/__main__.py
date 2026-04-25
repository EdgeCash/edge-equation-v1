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
  --email-preview  Route every would-be publish to email only (requires SMTP env).
                   Body is byte-identical to what XPublisher would post.
"""
import argparse
import csv
import json
import os
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
    DOMESTIC_LEAGUES,
    LEAGUES_FOR_CARD_TYPE,
    OVERSEAS_LEAGUES,
    ScheduledRunner,
    load_prior_daily_edge_picks,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.realization_store import RealizationStore
from edge_equation.posting.ai_graphic_prompt import build_ai_graphic_prompt
from edge_equation.posting.grade_track_record import (
    compute_track_record,
    format_track_record,
)
from edge_equation.posting.ledger import LedgerStore
from edge_equation.posting.ledger_recap import (
    collect_yesterday_recap,
    format_daily_recap,
    recap_to_public_dict,
)
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.posting.premium_daily_body import format_premium_daily
from edge_equation.publishing.email_publisher import EmailPublisher
from edge_equation.publishing.x_formatter import format_card as format_x_text
from edge_equation.utils.logging import get_logger


# Cards that, when previewed via email, should include the AI graphic
# prompt in the body so the user can paste it straight into an image
# generator. Matches the user-facing spec for daily / evening / overseas.
_AI_PROMPT_CARD_TYPES = frozenset({
    "daily_edge", "evening_edge", "overseas_edge",
})


def _email_preview_body(card: dict) -> str:
    """Email body for --email-preview. Section 1 is always the exact
    X post text. Section 2 -- only for the three slate cards -- is a
    copy-paste AI image-generation prompt."""
    x_text = format_x_text(card)
    if card.get("card_type") not in _AI_PROMPT_CARD_TYPES:
        return x_text
    prompt = build_ai_graphic_prompt(card)
    separator = "\n" + ("=" * 64) + "\n"
    return (
        "=== TEXT OF WHAT WOULD POST TO X ===\n\n"
        + x_text
        + separator
        + "=== AI GRAPHIC PROMPT -- paste into DALL-E / Midjourney / etc. ===\n\n"
        + prompt
    )


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
    """Resolve the league list for a cadence card.

    Slate separation is enforced here: every US-majors cadence card
    (Ledger, Daily Edge, Spotlight, Evening Edge) defaults to
    DOMESTIC_LEAGUES, while Overseas Edge defaults to OVERSEAS_LEAGUES.
    An explicit --leagues flag always wins so a maintainer can still
    run cross-slate diagnostics manually.
    """
    if explicit:
        return _parse_leagues(explicit)
    default = LEAGUES_FOR_CARD_TYPE.get(card_type, DOMESTIC_LEAGUES)
    return list(default)


def _email_preview_publisher(card_type: str) -> EmailPublisher:
    """Single-target publisher that emails the exact X post text. Used by
    --email-preview so you can audit every cadence card in your inbox
    before flipping X credentials on. For daily / evening / overseas
    cards the body also carries an AI-graphic prompt the operator can
    paste into an image generator for manual posting.

    Deliberately uses a FILE-ONLY failsafe (not the default composite)
    because the primary leg here IS SMTP -- if the SMTP send fails,
    retrying the same SMTP via the composite's SMTP leg would just fail
    again. The file failsafe preserves the rendered post text so an
    operator can see what would have gone out.
    """
    from edge_equation.publishing.failsafe import FileFailsafe
    return EmailPublisher(
        body_formatter=_email_preview_body,
        subject_prefix="[X-PREVIEW]",
        failsafe=FileFailsafe(),
    )


def _run_slate(args: argparse.Namespace, card_type: str) -> int:
    conn = _open_db(args.db)
    public_mode = getattr(args, "public_mode", True)
    email_preview = getattr(args, "email_preview", False)
    try:
        run_dt = datetime.utcnow()
        leagues = _default_leagues_for(card_type, args.leagues)
        ledger_stats = None
        daily_recap_data: Optional[dict] = None
        daily_recap_text: Optional[str] = None
        if card_type == CARD_TYPE_LEDGER:
            # The Ledger post has two independent pieces:
            #   1. The card BODY = yesterday's cross-slot recap of every
            #      public projection we actually posted (Daily / Spotlight
            #      / Evening / Overseas).
            #   2. The FOOTER (same as every free post) = the all-time
            #      Season Ledger W-L-T + ROI line.
            ledger_stats = LedgerStore.compute(conn)
            recap = collect_yesterday_recap(conn, run_dt)
            daily_recap_data = recap_to_public_dict(recap)
            daily_recap_text = format_daily_recap(recap, run_dt)
        elif public_mode:
            # Every free-content card must carry the Season Ledger footer,
            # so compute it up front and flow it into build_card.
            ledger_stats = LedgerStore.compute(conn)

        # --email-preview forces the publish path on with an email-only
        # publisher, so the operator can audit every cadence card via SMTP
        # before any X credential is provisioned. The CLI-level compliance
        # gate is kept in play; the publisher-level gate is moot here
        # (EmailPublisher doesn't run compliance_test).
        if email_preview:
            args.publish = True
            args.dry_run = False

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

        runner_publishers = None
        if email_preview:
            runner_publishers = [_email_preview_publisher(card_type)]

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
            publishers=runner_publishers,
            force=getattr(args, "force", False),
            cached_only=getattr(args, "cached_only", False),
            daily_recap=daily_recap_data,
            daily_recap_text=daily_recap_text,
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
                daily_recap=daily_recap_data,
                daily_recap_text=daily_recap_text,
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


def _cmd_refresh_data(args: argparse.Namespace) -> int:
    """Warm the OddsCache so every downstream cadence run reads locally
    instead of burning a live Odds API credit. Intended to run 2-3x
    daily via the data-refresher workflow. Credentials come from the
    ambient env (THE_ODDS_API_KEY)."""
    from edge_equation.data_fetcher import SLATE_SPORTS, fetch_daily_data
    conn = _open_db(args.db)
    summary: dict = {"slates": {}}
    try:
        slates_raw = (args.slates or "domestic,overseas").split(",")
        slates = [s.strip() for s in slates_raw if s.strip() in SLATE_SPORTS]
        for slate in slates:
            bundle = fetch_daily_data(
                conn,
                slate=slate,
                public_mode=False,
                scrape=True,
                cached_only=False,
            )
            summary["slates"][slate] = {
                "odds_leagues": sorted(bundle.odds.keys()),
                "schedule_leagues": sorted(bundle.schedules.keys()),
                "scraper_leagues": sorted(bundle.scrapers.keys()),
                "odds_total_games": sum(len(v) for v in bundle.odds.values()),
                "schedule_total_events": sum(len(v) for v in bundle.schedules.values()),
            }
    finally:
        conn.close()
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_auto_settle(args: argparse.Namespace) -> int:
    """Nightly results job. Two things happen in lockstep:

      1. Pull completed-game scores into GameResultsStore so
         FeatureComposer has the history it needs to produce strength
         ratings -- the engine is blind without this, so every A+/A/A-
         grade depends on it.

      2. Walk every still-pending pick and settle it against the
         newly-ingested scores (ML, Total, Spread). Populates the
         Season Ledger footer + Grade Track Record numbers.

    --days controls how many days back to scan (default 1: just
    yesterday). --backfill switches to seed mode (default 30 days).
    --source picks the upstream data source:
        thesportsdb (default): all 8 leagues via TheSportsDB
        mlb_stats: MLB only, via MLB's official Stats API (free,
            comprehensive -- the better source for MLB once we trust it)
    """
    from edge_equation.engine.realization import RealizationTracker
    from edge_equation.stats.results import GameResultsStore
          
    source = getattr(args, "source", None) or "thesportsdb"
    if source not in ("thesportsdb", "mlb_stats", "nhle", "nba_stats"):
        print(
            f"error: --source must be 'thesportsdb', 'mlb_stats', 'nhle', or "
            f"'nba_stats', got {source!r}",
            file=sys.stderr,
        )
        return 2

    if source == "mlb_stats":
        from edge_equation.stats.mlb_stats_ingest import (
            MlbStatsResultsIngestor as Ingestor,
        )
    elif source == "nhle":
        from edge_equation.stats.nhle_ingest import (
            NhleResultsIngestor as Ingestor,
        )
    elif source == "nba_stats":
        from edge_equation.stats.nba_stats_ingest import (
            NbaStatsResultsIngestor as Ingestor,
        )
    else:
        from edge_equation.stats.thesportsdb_ingest import (
            TheSportsDBResultsIngestor as Ingestor,
        )

    conn = _open_db(args.db)
    try:
        days = int(args.days)
        backfill = bool(args.backfill)
        if backfill:
            summary = Ingestor.backfill(conn, days=days)
        else:
            from datetime import date as _date, timedelta
            target = _date.today() - timedelta(days=1)
            summary = Ingestor.ingest_day(conn, day=target)

        settle = RealizationTracker.settle_picks_from_game_results(conn)
        totals_by_league = {}
        for league in ("MLB", "NFL", "NHL", "NBA", "KBO", "NPB"):
            totals_by_league[league] = GameResultsStore.count_by_league(conn, league)
    finally:
        conn.close()

    print(json.dumps({
        "mode": "backfill" if backfill else "nightly",
        "source": source,
        "ingest": summary.to_dict(),
        "settled_picks": settle,
        "game_results_by_league": totals_by_league,
    }, indent=2, default=str))
    return 0


def _cmd_backfill_results(args: argparse.Namespace) -> int:
    """One-shot seed of historical game results. Forwards to the
    auto-settle machinery with backfill=True and a longer default
    window so a fresh deploy can populate ~30 days of history in
    a single run."""
    args.backfill = True
    return _cmd_auto_settle(args)


def _cmd_premium_daily(args: argparse.Namespace) -> int:
    """
    Build the Premium Daily email: every A+ / A / A- pick, parlay of
    the day, top 6 DFS props, yesterday's engine hit rate. Always sent
    via email (never X). Subscriber-only content; not public_mode, so
    edge + Kelly remain visible and the compliance gate does not apply.
    """
    from datetime import timedelta
    conn = _open_db(args.db)
    try:
        run_dt = datetime.utcnow()
        leagues = _parse_leagues(args.leagues) or list(DEFAULT_LEAGUES)

        # Run the slate in premium mode: public_mode=False so edge/Kelly
        # survive. Use a short-lived internal slate id so the same day's
        # premium email can be rebuilt on demand.
        slate_id = f"premium_daily_{run_dt.date().isoformat().replace('-', '')}"
        # If today's premium slate already exists, re-use its picks so the
        # email is idempotent within the day -- unless --force was passed,
        # in which case we rebuild.
        # Premium is cross-slate: subscribers see every A+ / A / A-
        # projection across EVERY sport (domestic + overseas). We drive
        # two separate runner passes and concatenate the picks so the
        # per-card slate-separation guard stays intact for the public
        # feed while premium gets the whole picture.
        from edge_equation.persistence.slate_store import SlateStore
        existing = SlateStore.get(conn, slate_id)
        force = getattr(args, "force", False)
        cached_only = getattr(args, "cached_only", False)
        all_picks: list = []
        for runner_card, runner_leagues in (
            (CARD_TYPE_DAILY, list(DOMESTIC_LEAGUES)),
            (CARD_TYPE_OVERSEAS_EDGE, list(OVERSEAS_LEAGUES)),
        ):
            try:
                runner_summary = ScheduledRunner.run(
                    card_type=runner_card,
                    conn=conn,
                    run_datetime=run_dt,
                    leagues=runner_leagues,
                    publish=False,
                    dry_run=True,
                    csv_dir=args.csv_dir,
                    prefer_mock=args.prefer_mock,
                    public_mode=False,
                    force=force,
                    cached_only=cached_only,
                )
            except ValueError:
                # No sources for this slate (e.g., all leagues lack
                # mock sources). Premium just skips that half -- better
                # than a crash on a thin day.
                continue
            slate_picks = PickStore.list_by_slate(conn, runner_summary.slate_id)
            all_picks.extend(r.to_pick() for r in slate_picks)

        # Yesterday's engine hit rate (UTC).
        yesterday = (run_dt - timedelta(days=1)).date().isoformat()
        health = RealizationTracker.hit_rate_since(conn, since_iso=yesterday)

        # Yesterday's cross-slot recap: pulled in for subscribers so
        # they see the previous day's publicly-posted projections plus
        # outcomes inside the same premium email.
        recap = collect_yesterday_recap(conn, run_dt)
        recap_data = recap_to_public_dict(recap)
        recap_text = format_daily_recap(recap, run_dt)

        # Grade Track Record: historical hit rate per-(sport, grade)
        # scoped to whichever sports actually appear in today's picks.
        # Shows subscribers the provable base rate behind each A+ / A /
        # A- label on the day's slate. Empty on fresh-deploy days and
        # the renderer skips the section gracefully.
        today_sports = sorted({p.sport for p in all_picks if p.sport})
        track_records = compute_track_record(
            conn, sports=today_sports or None,
        )
        track_record_text = format_track_record(track_records)

        card = PostingFormatter.build_card(
            card_type="premium_daily",
            picks=all_picks,
            generated_at=run_dt.isoformat(),
            public_mode=False,
            engine_health=health,
            daily_recap=recap_data,
            daily_recap_text=recap_text,
            grade_track_record=[r.to_dict() for r in track_records],
            grade_track_record_text=track_record_text,
        )

        # File-only failsafe: the primary leg here is SMTP, so routing
        # through the composite's SMTP leg on failure would just fail
        # again. A file artifact still preserves the rendered post text.
        from edge_equation.publishing.failsafe import FileFailsafe
        publisher = EmailPublisher(
            body_formatter=format_premium_daily,
            subject_prefix="[Premium]",
            failsafe=FileFailsafe(),
        )
        if args.email_preview or args.publish:
            result = publisher.publish_card(card, dry_run=False)
        else:
            result = publisher.publish_card(card, dry_run=True)
    finally:
        conn.close()

    print(json.dumps({
        "card_type": "premium_daily",
        "n_picks": len(card.get("picks") or []),
        "n_parlay_legs": len(card.get("parlay") or []),
        "n_top_props": len(card.get("top_props") or []),
        "engine_health": card.get("engine_health"),
        "publish_result": result.to_dict() if hasattr(result, "to_dict") else str(result),
    }, indent=2, default=str))
    if hasattr(result, "success") and not result.success and not getattr(result, "failsafe_triggered", False):
        return 1
    return 0


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


def _cmd_diag(args: argparse.Namespace) -> int:
    """Engine diagnostic snapshot -- read-only DB inventory.

    Answers the "is the engine actually live and saving picks?"
    question without requiring the operator to sign into GitHub and
    scrape workflow logs. Prints a single JSON document covering:

      * Total slates and slates grouped by card_type.
      * Total picks and picks grouped by sport / market_type / grade.
      * Date range (earliest and latest slate generated_at, earliest
        and latest pick recorded_at).
      * Realization counters: how many picks have a realization set
        vs still pending.
      * A small "recent activity" window: the last 5 slates and how
        many picks each produced.

    The workflow counterpart (diag.yml) is manual-only -- never
    scheduled, never publishes. Safe to run anytime.
    """
    conn = _open_db(args.db)
    try:
        cur = conn.cursor()

        def scalar(sql: str, *params):
            row = cur.execute(sql, params).fetchone()
            return row[0] if row else None

        def rows(sql: str, *params):
            return [dict(r) for r in cur.execute(sql, params).fetchall()]

        slates_total = scalar("SELECT COUNT(*) FROM slates")
        slates_by_card_type = rows(
            "SELECT card_type, COUNT(*) AS n "
            "FROM slates GROUP BY card_type ORDER BY n DESC"
        )
        slate_date_range = cur.execute(
            "SELECT MIN(generated_at) AS earliest, "
            "MAX(generated_at) AS latest FROM slates"
        ).fetchone()

        picks_total = scalar("SELECT COUNT(*) FROM picks")
        picks_by_sport = rows(
            "SELECT sport, COUNT(*) AS n "
            "FROM picks GROUP BY sport ORDER BY n DESC"
        )
        picks_by_market = rows(
            "SELECT market_type, COUNT(*) AS n "
            "FROM picks GROUP BY market_type ORDER BY n DESC"
        )
        picks_by_grade = rows(
            "SELECT grade, COUNT(*) AS n "
            "FROM picks GROUP BY grade ORDER BY n DESC"
        )
        pick_date_range = cur.execute(
            "SELECT MIN(recorded_at) AS earliest, "
            "MAX(recorded_at) AS latest FROM picks"
        ).fetchone()

        realizations_total = scalar("SELECT COUNT(*) FROM realizations")
        # A pick is "settled" iff a matching row exists in the
        # realizations table (CSV / manual outcome recording) OR the
        # game is marked final in game_results (auto-settle path).
        # We don't infer settlement from the picks.realization column
        # because that field is initialized to a grade-based forecast
        # at pick creation (B=52, A=56 etc.) and only C-grade picks
        # start at the PENDING_DEFAULT value of 47.
        picks_with_csv_realization = scalar(
            "SELECT COUNT(*) FROM picks p WHERE EXISTS ("
            "  SELECT 1 FROM realizations r "
            "  WHERE r.game_id = p.game_id "
            "    AND r.market_type = p.market_type "
            "    AND r.selection = p.selection)"
        )
        picks_with_final_game = scalar(
            "SELECT COUNT(DISTINCT p.id) FROM picks p "
            "JOIN game_results g ON g.game_id = p.game_id "
            "WHERE g.status = 'final'"
        )

        recent_slates = rows(
            "SELECT s.slate_id, s.card_type, s.generated_at, "
            "  (SELECT COUNT(*) FROM picks p WHERE p.slate_id = s.slate_id) AS n_picks "
            "FROM slates s ORDER BY s.generated_at DESC LIMIT 5"
        )

        # Historical game results -- the source the engine's Pythagorean
        # / form / Elo ratings are built from. If this table is sparse
        # or empty, every team's Bradley-Terry strength collapses toward
        # ~1.0 and the engine finds phantom edges vs the market.
        game_results_total = scalar("SELECT COUNT(*) FROM game_results")
        game_results_by_league = rows(
            "SELECT league, COUNT(*) AS n, "
            "  MIN(start_time) AS earliest, MAX(start_time) AS latest "
            "FROM game_results GROUP BY league ORDER BY n DESC"
        )
        game_results_finaled = scalar(
            "SELECT COUNT(*) FROM game_results WHERE status = 'final'"
        )

        summary = {
            "slates": {
                "total": slates_total,
                "by_card_type": slates_by_card_type,
                "earliest": slate_date_range["earliest"] if slate_date_range else None,
                "latest": slate_date_range["latest"] if slate_date_range else None,
            },
            "picks": {
                "total": picks_total,
                "by_sport": picks_by_sport,
                "by_market_type": picks_by_market,
                "by_grade": picks_by_grade,
                "earliest": pick_date_range["earliest"] if pick_date_range else None,
                "latest": pick_date_range["latest"] if pick_date_range else None,
            },
            "realizations": {
                "rows_in_realizations_table": realizations_total,
                "picks_with_csv_realization": picks_with_csv_realization,
                "picks_with_final_game_result": picks_with_final_game,
            },
            "game_results": {
                "total": game_results_total,
                "finalized": game_results_finaled,
                "by_league": game_results_by_league,
            },
            "recent_slates": recent_slates,
        }
    finally:
        conn.close()
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_picks_csv(args: argparse.Namespace) -> int:
    """Dump picks for a given date to a CSV for operator QC.

    One row per pick, sorted by grade tier (A+ > A > A- > B > C > D > F)
    then by edge descending within the tier. Columns are the same
    whether the pick has been settled yet or not -- the settlement
    columns (Result, Final Score, Units +/-) are blank for pending
    games and filled in after the nightly results-settler has run.

    Scope: picks whose slate.generated_at falls on --date (UTC). If
    --date is omitted, today's UTC date is used.
    """
    import csv as _csv
    import os as _os
    import sys as _sys
    import math as _math
    from datetime import datetime, timezone

    # ---- date window ----------------------------------------------------
    target_date = args.date or datetime.now(timezone.utc).date().isoformat()

    # ---- output destination --------------------------------------------
    out_path = args.out or f"picks_{target_date}.csv"

    # ---- grade ordering -------------------------------------------------
    _GRADE_RANK = {"A+": 0, "A": 1, "A-": 2, "B": 3, "C": 4, "D": 5, "F": 6}

    def _grade_rank(grade):
        return _GRADE_RANK.get(grade or "", 99)

    # ---- helpers --------------------------------------------------------
    def _implied_prob(american_odds):
        """American odds -> implied probability as Decimal."""
        from decimal import Decimal
        if american_odds is None:
            return None
        odds = int(american_odds)
        if odds > 0:
            return Decimal(100) / Decimal(odds + 100)
        return Decimal(-odds) / Decimal(-odds + 100)

    def _units_pl(grade_row, odds, result):
        """Flat 1-unit P&L given American odds and a result.

        Win at -150 returns +0.67u, at +180 returns +1.80u.
        Loss is -1.00u. Push / pending is 0.00u.
        """
        if result not in ("W", "L"):
            return "0.00"
        if odds is None:
            return ""
        o = int(odds)
        if result == "W":
            if o > 0:
                return f"{o / 100:+.2f}"
            return f"{100 / -o:+.2f}"
        return "-1.00"

    def _format_projection(market_type, fair_prob, expected_value):
        """Single-column projection with market-appropriate units."""
        if fair_prob is not None:
            return f"{float(fair_prob) * 100:.1f}%"
        if expected_value is None:
            return ""
        # Rate / total markets -- include a unit label for readability.
        suffix_by_market = {
            "Total": " runs/pts",
            "Game_Total": " runs/pts",
            "K": " K",
            "HR": " HR",
            "Passing_Yards": " yd",
            "Rushing_Yards": " yd",
            "Receiving_Yards": " yd",
            "Points": " pts",
            "Rebounds": " reb",
            "Assists": " ast",
            "SOG": " SOG",
        }
        suffix = suffix_by_market.get(market_type, "")
        return f"{float(expected_value):.2f}{suffix}"

    def _mc_band(metadata):
        """Extract MC band as P10-P90 string if present in metadata."""
        mc = (metadata or {}).get("mc_stability") or {}
        p10 = mc.get("p10")
        p90 = mc.get("p90")
        if p10 is None or p90 is None:
            return ""
        try:
            return f"{float(p10):.2f}-{float(p90):.2f}"
        except (TypeError, ValueError):
            return ""

    def _sanity_rejected(metadata):
        return "Yes" if (metadata or {}).get("sanity_rejected_reason") else ""

    def _key_factors(metadata):
        return ((metadata or {}).get("read_notes") or "").strip()

    # ---- query ---------------------------------------------------------
    conn = _open_db(args.db)
    try:
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT p.*, s.card_type AS slate_card_type,
                   s.generated_at AS slate_generated_at
              FROM picks p
              LEFT JOIN slates s ON s.slate_id = p.slate_id
             WHERE date(COALESCE(s.generated_at, p.recorded_at)) = ?
            """,
            (target_date,),
        ).fetchall()

        # Settlement lookups keyed by game_id.
        game_results = {
            r["game_id"]: r
            for r in cur.execute(
                "SELECT game_id, home_team, away_team, home_score, "
                "away_score, status FROM game_results"
            ).fetchall()
        }

        records = []
        for row in rows:
            import json as _json
            from decimal import Decimal
            meta = {}
            if row["metadata_json"]:
                try:
                    meta = _json.loads(row["metadata_json"])
                except (ValueError, TypeError):
                    meta = {}
            fair_prob = Decimal(row["fair_prob"]) if row["fair_prob"] else None
            expected_value = (
                Decimal(row["expected_value"]) if row["expected_value"] else None
            )
            edge = Decimal(row["edge"]) if row["edge"] else None
            kelly = Decimal(row["kelly"]) if row["kelly"] else None
            implied = _implied_prob(row["odds"])

            home = meta.get("home_team") or ""
            away = meta.get("away_team") or ""
            matchup = (
                f"{away} @ {home}" if home and away else (row["game_id"] or "")
            )

            # Settlement status from picks.realization and game_results.
            gr = game_results.get(row["game_id"])
            result = ""
            if gr and gr["status"] == "final":
                # Use RealizationTracker logic if possible, but just showing
                # W/L/Push/Pending based on realization value (same mapping
                # the nightly settler uses).
                r_val = row["realization"]
                if r_val is None or r_val == 47:
                    result = "Pending"
                elif r_val >= 60:
                    result = "W"
                elif r_val <= 30:
                    result = "L"
                else:
                    result = "Push"
            else:
                result = "Pending"

            final_score = ""
            if gr and gr["status"] == "final":
                final_score = (
                    f"{gr['home_team']} {gr['home_score']}, "
                    f"{gr['away_team']} {gr['away_score']}"
                )

            records.append({
                "Date": target_date,
                "Card": row["slate_card_type"] or "",
                "Sport": row["sport"] or "",
                "Matchup": matchup,
                "Market": row["market_type"] or "",
                "Selection": row["selection"] or "",
                "Line": "" if row["line_number"] is None else str(row["line_number"]),
                "Odds": "" if row["odds"] is None else str(row["odds"]),
                "Engine Projection": _format_projection(
                    row["market_type"], fair_prob, expected_value,
                ),
                "Implied %": "" if implied is None else f"{float(implied) * 100:.1f}%",
                "Edge %": "" if edge is None else f"{float(edge) * 100:+.2f}%",
                "Kelly %": "" if kelly is None else f"{float(kelly) * 100:.2f}%",
                "Grade": row["grade"] or "",
                "MC Band (P10-P90)": _mc_band(meta),
                "Sanity Rejected": _sanity_rejected(meta),
                "Key Factors": _key_factors(meta),
                "Result": result,
                "Final Score": final_score,
                "Units +/-": _units_pl(
                    row["grade"], row["odds"], result,
                ),
                "_grade_rank": _grade_rank(row["grade"]),
                "_edge_sort": -float(edge) if edge is not None else 0.0,
            })

        # Sort: grade tier asc (A+ first), then edge desc within tier.
        records.sort(key=lambda r: (r["_grade_rank"], r["_edge_sort"]))
        # Strip sort helpers from final output.
        for r in records:
            r.pop("_grade_rank", None)
            r.pop("_edge_sort", None)
    finally:
        conn.close()

    if not records:
        _sys.stderr.write(
            f"No picks found for date={target_date}. Wrote empty CSV.\n"
        )
        # Still emit the header so downstream consumers don't choke.
        fieldnames = [
            "Date", "Card", "Sport", "Matchup", "Market", "Selection",
            "Line", "Odds", "Engine Projection", "Implied %", "Edge %",
            "Kelly %", "Grade", "MC Band (P10-P90)", "Sanity Rejected",
            "Key Factors", "Result", "Final Score", "Units +/-",
        ]
    else:
        fieldnames = list(records[0].keys())

    # Ensure parent dir exists if caller asked for a nested path.
    parent = _os.path.dirname(_os.path.abspath(out_path))
    if parent:
        _os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = _csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(r)

    print(f"Wrote {len(records)} picks to {out_path}")
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
    p.add_argument("--force", action="store_true", default=False,
                   help="Rebuild + republish even if a slate with the same id "
                        "already exists in the DB. Manual dispatches set this "
                        "so a cached DB doesn't block a second same-day run; "
                        "scheduled cron jobs leave it OFF so double-posts are "
                        "prevented.")
    p.add_argument("--cached-only", dest="cached_only",
                   action="store_true", default=False,
                   help="Read odds / schedules / scrapers from local cache "
                        "ONLY. On a cache miss, the slate is empty rather "
                        "than hitting the live Odds API. Every cadence "
                        "workflow passes this so the refresher is the sole "
                        "live-API consumer and free-tier credits stay intact.")
    p.add_argument("--email-preview", dest="email_preview",
                   action="store_true", default=False,
                   help="Route every would-be publish to email instead of X/Discord. "
                        "Body is the exact X post text. Forces --publish --no-dry-run "
                        "and bypasses X credentials entirely. Requires SMTP env vars.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edge-equation")
    parser.add_argument(
        "--debug", action="store_true", default=False,
        help="Enable DEBUG=1: per-market [OUTPUT]/[SKIPPED] prints in the "
             "slate runner, [DEBUG] prints in ProbabilityCalculator, and a "
             "final TEST SUMMARY after the command finishes.",
    )
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

    p_prem = sub.add_parser(
        "premium-daily",
        help="Build and email the Premium Daily card (subscribers only)",
    )
    _add_slate_flags(p_prem)
    p_prem.set_defaults(func=_cmd_premium_daily)

    p_refresh = sub.add_parser(
        "refresh-data",
        help="Pull live odds + schedules + scrapers into the OddsCache. "
             "Runs 2-3x/day; cadence workflows read from this cache.",
    )
    p_refresh.add_argument("--db", type=str, default=None)
    p_refresh.add_argument(
        "--slates", type=str, default="domestic,overseas",
        help="Comma-separated slate names to refresh (default: domestic,overseas)",
    )
    p_refresh.set_defaults(func=_cmd_refresh_data)

    p_auto = sub.add_parser(
        "auto-settle",
        help="Pull yesterday's completed scores from TheSportsDB -> "
             "GameResultsStore, then settle matching picks' realization "
             "by comparing scores to each pick's selection. Runs nightly.",
    )
    p_auto.add_argument("--db", type=str, default=None)
    p_auto.add_argument("--days", type=int, default=1,
                        help="Days back from today to scan (default 1).")
    p_auto.add_argument("--backfill", action="store_true", default=False,
                        help="Seed mode: scan --days days back (default 30 "
                             "when used with backfill-results).")
    p_auto.add_argument(
        "--source", type=str, default="thesportsdb",
        choices=("thesportsdb", "mlb_stats", "nhle", "nba_stats"),
                help="Data source for game scores. 'thesportsdb' (default) "
             "covers all 8 leagues but with thin coverage. "
             "'mlb_stats' = MLB free official API. "
             "'nhle' = NHL free official API. "
             "'nba_stats' = NBA free official Stats API.",
    )
    p_auto.set_defaults(func=_cmd_auto_settle)

    p_backfill = sub.add_parser(
        "backfill-results",
        help="One-time seed of historical game results (default 30 days).",
    )
    p_backfill.add_argument("--db", type=str, default=None)
    p_backfill.add_argument("--days", type=int, default=30)
    p_backfill.add_argument(
        "--source", type=str, default="thesportsdb",
        choices=("thesportsdb", "mlb_stats", "nhle"),
        help="Data source for game scores. See `auto-settle --help`.",
    )
    p_backfill.set_defaults(func=_cmd_backfill_results)

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

    p_diag = sub.add_parser(
        "diag",
        help="Print a JSON snapshot of slates / picks / realizations "
             "already persisted in the DB (read-only).",
    )
    p_diag.add_argument("--db", type=str, default=None)
    p_diag.set_defaults(func=_cmd_diag)

    p_csv = sub.add_parser(
        "picks-csv",
        help="Dump picks for a given date to a CSV spreadsheet for "
             "operator QC. Columns cover projection, grade, edge, "
             "Kelly, MC band, and (once settled) result + units P&L.",
    )
    p_csv.add_argument("--db", type=str, default=None)
    p_csv.add_argument(
        "--date", type=str, default=None,
        help="UTC date in YYYY-MM-DD form. Default: today (UTC).",
    )
    p_csv.add_argument(
        "--out", type=str, default=None,
        help="Output path. Default: picks_<date>.csv in the working dir.",
    )
    p_csv.set_defaults(func=_cmd_picks_csv)

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
    debug_mode = getattr(args, "debug", False)
    if debug_mode:
        os.environ["DEBUG"] = "1"
        # Lazy import so slate_runner isn't pulled in for subcommands that
        # never touch it (e.g. settle / load-results).
        from edge_equation.engine import slate_runner
        slate_runner.reset_debug_stats()
    if getattr(args, "func", None) is None:
        # Legacy back-compat: invoke the pipeline demo.
        rc = _cmd_pipeline(argparse.Namespace(mode=getattr(args, "mode", "daily")))
    else:
        rc = args.func(args)
    if debug_mode:
        from edge_equation.engine import slate_runner
        stats = slate_runner.get_debug_stats()
        seen = ", ".join(stats["supported_markets_seen"]) or "(none)"
        print("=== TEST SUMMARY ===")
        print(f"Markets processed: {stats['markets_processed']}")
        print(f"Markets with picks: {stats['markets_with_picks']}")
        print(f"Supported markets seen: {seen}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
