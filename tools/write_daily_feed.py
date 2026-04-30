"""Write the unified daily picks feed for the website.

Produces ``website/public/data/daily/latest.json`` in the v1 schema
documented at ``website/public/data/daily/README.md``. The website's
loader (``website/lib/daily-feed.ts``) reads that file as the primary
data source and falls back to the FastAPI archive when it's missing.

Usage::

    python -m tools.write_daily_feed                           # today
    python -m tools.write_daily_feed --date 2026-04-30         # specific date
    python -m tools.write_daily_feed --out path/to/latest.json # custom path
    python -m tools.write_daily_feed --dry-run                 # print only

Wiring into the cron::

    python -m edge_equation.run_daily            # send the email
    python -m tools.write_daily_feed             # update the website feed

Or fold the second call into ``run_daily.py`` once the cron is happy.

Schema contract
---------------

Always emit ``version=1``. Bump only on a breaking change — the
website refuses non-matching versions and falls back to the archive.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DAILY_FEED_VERSION = 1

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "website" / "public" / "data" / "daily" / "latest.json"

log = logging.getLogger("write_daily_feed")


# ---------------------------------------------------------------------------
# Schema (mirrors website/lib/daily-feed.ts).
# ---------------------------------------------------------------------------


@dataclass
class FeedLine:
    number: str | None
    odds: int | None


@dataclass
class FeedPick:
    id: str
    sport: str
    market_type: str
    selection: str
    line: FeedLine
    fair_prob: str | None
    edge: str | None
    kelly: str | None
    grade: str
    tier: str | None = None
    notes: str | None = None
    event_time: str | None = None
    game_id: str | None = None


@dataclass
class DailyFeed:
    version: int
    generated_at: str
    date: str
    source: str
    picks: list[FeedPick]
    notes: str | None = None


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


# ---------------------------------------------------------------------------
# Adapters — turn engine pick dicts into FeedPick.
#
# Engine pick dicts come from ``edge_equation.engines.nrfi.email_report.
# _to_card_pick`` (NRFI/YRFI) and the analogous props/full-game shapes.
# Each adapter is small and explicit so a developer can extend it
# without reverse-engineering the schema.
# ---------------------------------------------------------------------------


def _format_decimal(value: float | None, places: int = 4) -> str | None:
    if value is None:
        return None
    return f"{float(value):.{places}f}"


def _strip_units(s: str | None, suffix: str) -> float | None:
    if s is None:
        return None
    s = s.strip()
    if s.endswith(suffix):
        s = s[: -len(suffix)]
    s = s.replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def adapt_nrfi_pick(p: dict[str, Any]) -> FeedPick:
    """Adapt one NRFI/YRFI pick dict from email_report._to_card_pick."""
    market = str(p.get("market_type", "")).upper()
    game_id = str(p.get("game_id", ""))
    pct_label = f"{p.get('pct', '')}".strip()
    selection = (
        f"{market} · {game_id}" if game_id else market
    )

    edge_raw = p.get("edge_pp_raw")
    edge_decimal = (edge_raw / 100.0) if edge_raw is not None else None

    kelly_raw = p.get("kelly_units_raw")
    # kelly_units is in units, not a probability — keep as-is in the
    # ``kelly`` field, which the website displays via formatPercent.
    # If your engine emits a fraction (0..1), use it directly.

    return FeedPick(
        id=f"{game_id or 'nrfi'}-{market}",
        sport="MLB",
        market_type=market,
        selection=selection or pct_label or market,
        line=FeedLine(number=None, odds=None),
        fair_prob=_format_decimal(p.get("fair_prob") and float(p["fair_prob"])),
        edge=_format_decimal(edge_decimal),
        kelly=_format_decimal(kelly_raw, places=4),
        grade=str(p.get("grade", "")) or "C",
        tier=str(p["tier"]) if p.get("tier") else None,
        notes=p.get("drivers") or None,
        event_time=None,
        game_id=game_id or None,
    )


def adapt_props_pick(p: dict[str, Any]) -> FeedPick:
    """Adapter for props pick dicts. Extend as the props engine evolves."""
    return FeedPick(
        id=str(p.get("id") or f"prop-{p.get('player', '?')}-{p.get('market', '?')}"),
        sport=str(p.get("sport", "MLB")),
        market_type=str(p.get("market_type", "PLAYER_PROP")),
        selection=str(p.get("selection", "")),
        line=FeedLine(
            number=str(p["line_number"]) if p.get("line_number") is not None else None,
            odds=int(p["line_odds"]) if p.get("line_odds") is not None else None,
        ),
        fair_prob=_format_decimal(p.get("fair_prob")),
        edge=_format_decimal(p.get("edge")),
        kelly=_format_decimal(p.get("kelly")),
        grade=str(p.get("grade", "C")),
        tier=p.get("tier"),
        notes=p.get("notes"),
        event_time=p.get("event_time"),
        game_id=p.get("game_id"),
    )


def adapt_fullgame_pick(p: dict[str, Any]) -> FeedPick:
    """Adapter for full-game pick dicts. Extend as the engine evolves."""
    return FeedPick(
        id=str(p.get("id") or f"fg-{p.get('game_id', '?')}-{p.get('market_type', '?')}"),
        sport=str(p.get("sport", "MLB")),
        market_type=str(p.get("market_type", "MONEYLINE")),
        selection=str(p.get("selection", "")),
        line=FeedLine(
            number=str(p["line_number"]) if p.get("line_number") is not None else None,
            odds=int(p["line_odds"]) if p.get("line_odds") is not None else None,
        ),
        fair_prob=_format_decimal(p.get("fair_prob")),
        edge=_format_decimal(p.get("edge")),
        kelly=_format_decimal(p.get("kelly")),
        grade=str(p.get("grade", "C")),
        tier=p.get("tier"),
        notes=p.get("notes"),
        event_time=p.get("event_time"),
        game_id=p.get("game_id"),
    )


# ---------------------------------------------------------------------------
# Engine integration.
# ---------------------------------------------------------------------------


def gather_picks(target_date: str, *, run_etl: bool = True) -> list[FeedPick]:
    """Collect today's picks from every engine that exposes structured output.

    Currently:
      - NRFI/YRFI: pulled from ``email_report`` via ``_collect_nrfi_picks``.
      - Props / full-game: extension points; wire your engines here.

    Errors in any one engine are logged and swallowed so a single
    broken source doesn't take the whole feed down.
    """
    picks: list[FeedPick] = []

    # NRFI / YRFI ---------------------------------------------------------
    try:
        nrfi_picks = _collect_nrfi_picks(target_date, run_etl=run_etl)
        picks.extend(adapt_nrfi_pick(p) for p in nrfi_picks)
    except Exception as e:  # noqa: BLE001
        log.warning("NRFI pick collection failed: %s", e)

    # Props ---------------------------------------------------------------
    try:
        props_picks = _collect_props_picks(target_date)
        picks.extend(adapt_props_pick(p) for p in props_picks)
    except Exception as e:  # noqa: BLE001
        log.warning("props pick collection failed: %s", e)

    # Full-game -----------------------------------------------------------
    try:
        fg_picks = _collect_fullgame_picks(target_date)
        picks.extend(adapt_fullgame_pick(p) for p in fg_picks)
    except Exception as e:  # noqa: BLE001
        log.warning("full-game pick collection failed: %s", e)

    return picks


def _collect_nrfi_picks(target_date: str, *, run_etl: bool) -> list[dict[str, Any]]:
    """Run the NRFI engine for ``target_date`` and return the structured
    pick dicts the email card already produces.

    Mirrors the head of ``email_report.build_card`` so we don't re-render
    text, just collect the picks list.
    """
    from edge_equation.engines.nrfi.email_report import _to_card_pick  # type: ignore
    from edge_equation.engines.nrfi.bridge import (  # type: ignore
        run_engine_for_date,
    )

    bridge_outputs, label_map = run_engine_for_date(target_date, run_etl=run_etl)
    return [_to_card_pick(o, "ml", label_map) for o in bridge_outputs]


def _collect_props_picks(target_date: str) -> list[dict[str, Any]]:
    """Hook point for the props engine. Returns [] until wired."""
    try:
        from edge_equation.engines.props_prizepicks.daily import (  # type: ignore
            collect_picks_for_feed,
        )
    except ImportError:
        return []
    return collect_picks_for_feed(target_date)


def _collect_fullgame_picks(target_date: str) -> list[dict[str, Any]]:
    """Hook point for the full-game engine. Returns [] until wired."""
    try:
        from edge_equation.engines.fullgame.daily import (  # type: ignore
            collect_picks_for_feed,
        )
    except ImportError:
        return []
    return collect_picks_for_feed(target_date)


# ---------------------------------------------------------------------------
# Build + write.
# ---------------------------------------------------------------------------


def build_feed(target_date: str, *, run_etl: bool = True) -> DailyFeed:
    return DailyFeed(
        version=DAILY_FEED_VERSION,
        generated_at=_now_iso(),
        date=target_date,
        source="run_daily.py",
        picks=gather_picks(target_date, run_etl=run_etl),
    )


def feed_to_json(feed: DailyFeed) -> str:
    payload = asdict(feed)
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_feed(feed: DailyFeed, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(feed_to_json(feed), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--date", default=_today_iso(), help="YYYY-MM-DD slate date.")
    p.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output path (default: website/public/data/daily/latest.json).",
    )
    p.add_argument(
        "--no-etl",
        action="store_true",
        help="Skip ETL step in the NRFI engine (assumes data is already fresh).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the feed JSON to stdout instead of writing the file.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    feed = build_feed(args.date, run_etl=not args.no_etl)
    if args.dry_run:
        sys.stdout.write(feed_to_json(feed))
        return 0
    out = Path(args.out)
    write_feed(feed, out)
    log.info("wrote %s (%d picks)", out, len(feed.picks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
