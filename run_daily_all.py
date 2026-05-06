#!/usr/bin/env python3
"""Master daily runner — every engine in one pass.

Wires every sport's unified daily runner to a single command:

    python run_daily_all.py --all

What it does, in order:

1. Walks the ``ENGINE_CATALOG`` list (one entry per sport).
2. For each entry, calls the sport's ``build_unified_<sport>_card``
   function with today's date. Per-row engine outputs (the same
   side-effects each ``run_daily_<sport>.py --all`` produces) land
   in their per-sport directories.
3. Runs the website unified-feed exporter ONCE at the end with
   ``include_wnba=True``, ``include_nfl=True``, ``include_ncaaf=True``
   so every sport's section lives in a single
   ``website/public/data/daily/latest.json`` instead of being
   overwritten by whichever sport ran last.

Adding a new engine = adding ONE entry to ``ENGINE_CATALOG``. The
GitHub Actions master workflow (``.github/workflows/daily-master.yml``)
calls this script and commits every output path the catalog
declares — no workflow edit needed when a new sport ships.

CLI::

    python run_daily_all.py             # card preview, no website export
    python run_daily_all.py --all        # full pipeline + website feed
    python run_daily_all.py --all --date 2026-09-04
    python run_daily_all.py --all --only mlb,wnba
    python run_daily_all.py --all --skip ncaaf
"""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Callable, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


DEFAULT_FEED_OUT = (
    REPO_ROOT / "website" / "public" / "data" / "daily" / "latest.json"
)


# ---------------------------------------------------------------------------
# Engine catalog — single source of truth.
#
# Adding a new sport: append one entry. The build_card_module string
# is the dotted Python module that exposes ``build_unified_<sport>_card``;
# `feature_flag_env` and `output_dirs` are read by the GitHub Actions
# master workflow so it knows which env vars to set and which paths to
# commit.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineEntry:
    """One sport's wiring for the master daily runner."""

    sport: str
    label: str
    build_card_module: str
    build_card_attr: str
    output_dirs: tuple[str, ...]
    feature_flag_env: Optional[str] = None
    feature_flag_value: str = "on"
    bundle_kwarg: Optional[str] = None      # e.g. "include_wnba"


ENGINE_CATALOG: list[EngineEntry] = [
    EngineEntry(
        sport="mlb",
        label="MLB",
        build_card_module="edge_equation.engines.mlb.run_daily",
        build_card_attr="build_unified_mlb_card",
        output_dirs=("website/public/data/mlb/",),
        # MLB engine is always live; no feature flag.
        bundle_kwarg=None,                   # MLB picks land via the
                                               # default build_bundle path.
    ),
    EngineEntry(
        sport="wnba",
        label="WNBA",
        build_card_module="edge_equation.engines.wnba.parlay_runner",
        build_card_attr="build_unified_wnba_card",
        output_dirs=("website/public/data/wnba/",),
        feature_flag_env="EDGE_FEATURE_WNBA_PARLAYS",
        bundle_kwarg="include_wnba",
    ),
    EngineEntry(
        sport="nfl",
        label="NFL",
        build_card_module="edge_equation.engines.nfl.parlay_runner",
        build_card_attr="build_unified_nfl_card",
        output_dirs=("website/public/data/nfl/",),
        feature_flag_env="EDGE_FEATURE_NFL_PARLAYS",
        bundle_kwarg="include_nfl",
    ),
    EngineEntry(
        sport="ncaaf",
        label="NCAAF",
        build_card_module="edge_equation.engines.ncaaf.parlay_runner",
        build_card_attr="build_unified_ncaaf_card",
        output_dirs=("website/public/data/ncaaf/",),
        feature_flag_env="EDGE_FEATURE_NCAAF_PARLAYS",
        bundle_kwarg="include_ncaaf",
    ),
]


# ---------------------------------------------------------------------------
# Helpers exposed for the GitHub Actions workflow.
# ---------------------------------------------------------------------------


def list_sports() -> list[str]:
    """Sport keys in catalog order — used by --help and CI matrices."""
    return [e.sport for e in ENGINE_CATALOG]


def output_paths_to_commit() -> list[str]:
    """Every directory the master workflow should `git add` after a run.

    Includes per-sport `player_logs/` and `context_today/` dirs the
    profile-data writers populate, so adding a sport to the catalog
    auto-flows through to the workflow's commit step.
    """
    paths: list[str] = []
    for entry in ENGINE_CATALOG:
        paths.extend(entry.output_dirs)
        paths.append(f"website/public/data/{entry.sport}/player_logs/")
        paths.append(f"website/public/data/{entry.sport}/context_today/")
    paths.append("website/public/data/daily/")
    paths.append("website/public/data/calibration_alerts.json")
    # De-dupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def feature_flag_env() -> dict[str, str]:
    """Map of env-var name → value for every sport's feature flag.

    The GitHub Actions workflow exports these so each per-sport
    parlay engine is enabled inside the pipeline only.
    """
    return {
        e.feature_flag_env: e.feature_flag_value
        for e in ENGINE_CATALOG
        if e.feature_flag_env
    }


# ---------------------------------------------------------------------------
# Per-sport runner.
# ---------------------------------------------------------------------------


def _resolve_build_card(entry: EngineEntry) -> Callable:
    module = importlib.import_module(entry.build_card_module)
    return getattr(module, entry.build_card_attr)


def _format_card(entry: EngineEntry, card) -> str:
    """Best-effort plain-text card render. Each sport's runner module
    exposes a private `_format_card` helper; we reuse it when present
    and fall back to a one-liner."""
    try:
        module = importlib.import_module(entry.build_card_module)
        formatter = getattr(module, "_format_card", None)
        if callable(formatter):
            return formatter(card)
    except Exception:
        pass
    return f"{entry.label} card · target_date={getattr(card, 'target_date', '?')}\n"


def run_one_engine(
    entry: EngineEntry,
    target_date: Optional[str],
) -> tuple[str, bool]:
    """Run one engine. Returns (sport, success). Best-effort — a
    failing engine never blocks the others."""
    print("=" * 60)
    print(f"{entry.label.upper()} DAILY CARD  ·  {target_date or 'today'}")
    print("=" * 60)
    try:
        build_card = _resolve_build_card(entry)
    except Exception as e:
        print(
            f"[run_daily_all] {entry.sport}: build-card import failed "
            f"({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return entry.sport, False
    try:
        card = build_card(target_date)
    except Exception as e:  # pragma: no cover — defensive
        print(
            f"[run_daily_all] {entry.sport}: build-card call failed "
            f"({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return entry.sport, False
    print(_format_card(entry, card))
    return entry.sport, True


# ---------------------------------------------------------------------------
# Unified website feed exporter.
# ---------------------------------------------------------------------------


def export_unified_feed(
    target_date: str, *, sports: list[EngineEntry],
) -> Optional[Path]:
    """Write the merged ``daily/latest.json`` once at the end.

    Walks the catalog and toggles each sport's `include_*` kwarg on
    ``build_bundle`` based on whether it ran successfully — sports
    skipped via ``--only`` or ``--skip`` are excluded from the merged
    bundle so the website doesn't surface stale data.
    """
    try:
        from edge_equation.engines.website.build_daily_feed import (
            build_bundle, write_bundle,
        )
    except ImportError as exc:
        print(
            f"[run_daily_all] feed export skipped — {exc}.",
            file=sys.stderr,
        )
        return None

    kwargs: dict[str, bool] = {}
    for entry in sports:
        if entry.bundle_kwarg:
            kwargs[entry.bundle_kwarg] = True

    bundle = build_bundle(
        store=None, target_date=target_date,
        props_store=None, fullgame_store=None,
        **kwargs,
    )
    write_bundle(bundle, DEFAULT_FEED_OUT)
    return DEFAULT_FEED_OUT


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _filter_catalog(
    only: Optional[list[str]], skip: Optional[list[str]],
) -> list[EngineEntry]:
    chosen = ENGINE_CATALOG
    if only:
        only_set = {s.strip().lower() for s in only}
        chosen = [e for e in chosen if e.sport in only_set]
    if skip:
        skip_set = {s.strip().lower() for s in skip}
        chosen = [e for e in chosen if e.sport not in skip_set]
    return chosen


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run every engine's daily card in one pass. With --all, "
            "also writes the unified website daily feed."
        ),
    )
    parser.add_argument(
        "--all", dest="run_all", action="store_true",
        help="Run the full pipeline: every engine + unified website feed.",
    )
    parser.add_argument(
        "--date", default=None,
        help="Slate date YYYY-MM-DD. Default: today (UTC).",
    )
    parser.add_argument(
        "--only", default=None,
        help="Comma-separated sport keys to include. Default: every sport.",
    )
    parser.add_argument(
        "--skip", default=None,
        help="Comma-separated sport keys to exclude.",
    )
    parser.add_argument(
        "--list-sports", action="store_true",
        help="Print the engine catalog (one sport per line) and exit.",
    )
    parser.add_argument(
        "--list-output-paths", action="store_true",
        help=(
            "Print the directories the master workflow should commit, "
            "one per line, and exit."
        ),
    )
    parser.add_argument(
        "--skip-player-profiles", action="store_true",
        help=(
            "Skip the per-player game-log + context_today writers "
            "after the engines run. Useful for fast smoke runs."
        ),
    )
    parser.add_argument(
        "--skip-alerts", action="store_true",
        help=(
            "Skip the calibration-drift alert checker. Useful for "
            "fast smoke runs."
        ),
    )
    parser.add_argument(
        "--skip-email-digest", action="store_true",
        help=(
            "Skip the public email digest send. Useful for fast "
            "smoke runs (also a no-op when EDGE_FEATURE_EMAIL_DIGEST "
            "isn't 'on')."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list_sports:
        for s in list_sports():
            print(s)
        return 0
    if args.list_output_paths:
        for p in output_paths_to_commit():
            print(p)
        return 0

    target = args.date or _date.today().isoformat()
    only = args.only.split(",") if args.only else None
    skip = args.skip.split(",") if args.skip else None
    chosen = _filter_catalog(only, skip)

    if not chosen:
        print(
            "[run_daily_all] no sports selected — nothing to run.",
            file=sys.stderr,
        )
        return 0

    print(
        f"[run_daily_all] running {len(chosen)} engine(s) for {target}: "
        f"{', '.join(e.sport for e in chosen)}",
    )

    successful: list[EngineEntry] = []
    for entry in chosen:
        sport, ok = run_one_engine(entry, target)
        if ok:
            successful.append(entry)
        else:
            print(
                f"[run_daily_all] {sport} failed — continuing.",
                file=sys.stderr,
            )

    if args.run_all:
        out_path = export_unified_feed(target, sports=successful)
        if out_path is not None:
            print(f"[run_daily_all] unified feed written → {out_path}")
        else:
            print(
                "[run_daily_all] unified feed not written — see warnings above.",
            )
        # Walk the freshly-written unified feed and emit per-player
        # game-log + context JSON files so the website's player
        # profile pages have real data instead of "Limited data".
        if not args.skip_player_profiles:
            _build_player_profiles(
                target_date=target,
                sports=[e.sport for e in successful],
            )
        # Compute calibration-drift alerts so the website banner +
        # the GitHub Actions run summary surface any sport whose
        # Brier or ROI has drifted past the publish gate.
        if not args.skip_alerts:
            _build_calibration_alerts(
                sports=[e.sport for e in successful],
            )
        # Send today's email digest to the public subscriber list.
        # No-op when EDGE_FEATURE_EMAIL_DIGEST isn't 'on' or the
        # Resend env vars aren't set — soft-fail by design so a
        # missing key never crashes the daily build.
        if not args.skip_email_digest:
            _build_and_send_email_digest()
    return 0


def _build_player_profiles(
    *, target_date: str, sports: list[str],
) -> None:
    """Run the player-profile writers for today's slate.

    Best-effort. If the unified feed isn't readable or the writer
    module fails to import, we log a warning and exit cleanly so
    the rest of the master pipeline still wins.
    """
    try:
        from edge_equation.exporters.player_profiles import build_for_slate
    except Exception as e:
        print(
            f"[run_daily_all] player profiles skipped — import failed "
            f"({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return
    feed_path = (
        REPO_ROOT / "website" / "public" / "data" / "daily" / "latest.json"
    )
    if not feed_path.exists():
        print(
            "[run_daily_all] player profiles skipped — "
            "daily/latest.json not on disk yet.",
            file=sys.stderr,
        )
        return
    try:
        import json
        feed = json.loads(feed_path.read_text())
    except Exception as e:
        print(
            f"[run_daily_all] player profiles skipped — feed unreadable "
            f"({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return
    try:
        summary = build_for_slate(
            feed,
            target_date=target_date,
            sports=sports or None,
        )
        for sport, counts in summary.items():
            print(
                f"[run_daily_all] {sport} player profiles: "
                f"{counts.get('logs', 0)} logs · "
                f"{counts.get('context', 0)} context files",
            )
    except Exception as e:
        print(
            f"[run_daily_all] player profiles failed "
            f"({type(e).__name__}: {e}) — continuing.",
            file=sys.stderr,
        )


def _build_calibration_alerts(*, sports: list[str]) -> None:
    """Run the calibration-drift checker over the freshly-published
    picks log for each sport. Best-effort — any failure logs a
    warning and falls through.
    """
    try:
        from edge_equation.exporters.calibration_alerts import (
            build_report,
            log_alerts_to_console,
            write_report,
        )
    except Exception as e:
        print(
            f"[run_daily_all] calibration alerts skipped — import "
            f"failed ({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return
    try:
        report = build_report(
            sports=sports or ("mlb", "wnba", "nfl", "ncaaf"),
        )
        written = write_report(report)
        log_alerts_to_console(report)
        print(f"[run_daily_all] calibration alerts → {written}")
    except Exception as e:
        print(
            f"[run_daily_all] calibration alerts failed "
            f"({type(e).__name__}: {e}) — continuing.",
            file=sys.stderr,
        )


def _build_and_send_email_digest() -> None:
    """Build today's email digest from the unified daily feed and
    send it via Resend. Best-effort — a missing API key, missing
    audience, or missing FROM address all log a clean 'skipped'
    line and return without crashing the master pipeline.
    """
    try:
        from edge_equation.exporters.email_digest import (
            build_digest, load_config, send_digest,
        )
    except Exception as e:
        print(
            f"[run_daily_all] email digest skipped — import failed "
            f"({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return
    feed_path = (
        REPO_ROOT / "website" / "public" / "data" / "daily" / "latest.json"
    )
    if not feed_path.exists():
        print(
            "[run_daily_all] email digest skipped — daily/latest.json "
            "not on disk yet.",
            file=sys.stderr,
        )
        return
    try:
        import json as _json
        feed = _json.loads(feed_path.read_text())
    except Exception as e:
        print(
            f"[run_daily_all] email digest skipped — feed unreadable "
            f"({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return
    try:
        digest = build_digest(feed)
        config = load_config()
        result = send_digest(digest, config)
        if result.get("sent"):
            print(
                "[run_daily_all] email digest sent · broadcast_id="
                f"{result.get('broadcast_id')}",
            )
        elif result.get("skipped_reason"):
            print(
                "[run_daily_all] email digest skipped — "
                f"{result['skipped_reason']}",
            )
        else:
            print(
                "[run_daily_all] email digest FAILED — "
                f"{result.get('error')}",
                file=sys.stderr,
            )
    except Exception as e:
        print(
            f"[run_daily_all] email digest failed "
            f"({type(e).__name__}: {e}) — continuing.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    sys.exit(main())
