"""MLB Player-Props Daily Exporter.

Calls the props engine's `build_props_card` to project today's lines,
then writes JSON + CSV outputs that the website + Premium combined
spreadsheet can consume directly:

    website/public/data/props/
        props_daily.json    -- full payload (all picks + meta)
        todays_card.json    -- gate-passing high-conviction subset
        todays_card.csv     -- same, flat CSV
        backtest.json       -- per-tier ledger summary (when present)

Why a separate exporter?
    The MLB game-results pipeline writes `mlb_daily.json` already;
    props were only emitted into the email pipeline. This bridges the
    gap so the website surfaces props alongside game-results AND so
    the Premium "Bet This" combiner has a stable JSON to read.

Brand-aligned filters layered on top of the engine's tier ladder:

    --min-tier      Tier floor (default STRONG -- LEAN is content-only)
    --min-conviction  Calibrated model_prob floor (default 0.55)
    --min-edge      Edge in percentage points (default 5.0pp)

Usage::

    python -m edge_equation.exporters.props.daily
    python -m edge_equation.exporters.props.daily --date 2026-05-09
    python -m edge_equation.exporters.props.daily --min-conviction 0.60
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from edge_equation.engines.props_prizepicks.daily import build_props_card
from edge_equation.engines.tiering import Tier
from edge_equation.utils.logging import get_logger


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "website" / "public" / "data" / "props"

FEATURE_FLAG_ENV = "EDGE_FEATURE_PROPS_PIPELINE"

log = get_logger(__name__)


def _flag_on() -> bool:
    return os.environ.get(FEATURE_FLAG_ENV, "").strip().lower() in (
        "1", "on", "true", "yes",
    )


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_TIER_ORDER = {
    Tier.ELITE: 4, Tier.STRONG: 3, Tier.MODERATE: 2,
    Tier.LEAN: 1, Tier.NO_PLAY: 0,
}


def _pick_to_dict(pick) -> Dict[str, Any]:
    """Flatten a PropOutput into a JSON-serialisable row."""
    return {
        "market_type":         pick.market_type,
        "market_label":        pick.market_label,
        "player_name":         pick.player_name,
        "line_value":          pick.line_value,
        "side":                pick.side,
        "model_prob":          round(pick.model_prob, 4),
        "model_pct":           pick.model_pct,
        "market_prob":         round(pick.market_prob, 4),
        "market_prob_raw":     round(pick.market_prob_raw, 4),
        "vig_corrected":       pick.vig_corrected,
        "edge_pp":             round(pick.edge_pp, 2),
        "tier":                pick.tier,
        "grade":               pick.grade,
        "color_band":          pick.color_band,
        "color_hex":           pick.color_hex,
        "kelly_units":         pick.kelly_units,
        "american_odds":       pick.american_odds,
        "decimal_odds":        pick.decimal_odds,
        "book":                pick.book,
        "lam":                 round(pick.lam, 3),
        "blend_n":             pick.blend_n,
        "confidence":          round(pick.confidence, 3),
        "mc_low":              round(pick.mc_low, 4),
        "mc_high":             round(pick.mc_high, 4),
        "mc_band_pp":          round(pick.mc_band_pp, 2),
    }


def _premium_filter(
    rows: List[Dict[str, Any]],
    min_tier: Tier,
    min_conviction: float,
    min_edge_pp: float,
) -> List[Dict[str, Any]]:
    """Apply the elevated-bar filter Premium uses on top of the tier ladder."""
    floor = _TIER_ORDER[min_tier]
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            tier_rank = _TIER_ORDER[Tier(r["tier"])]
        except (KeyError, ValueError):
            tier_rank = 0
        if tier_rank < floor:
            continue
        if (r.get("model_prob") or 0.0) < min_conviction:
            continue
        if (r.get("edge_pp") or 0.0) < min_edge_pp:
            continue
        out.append(r)
    out.sort(key=lambda r: r.get("edge_pp", 0.0), reverse=True)
    return out


CSV_COLUMNS = [
    "tier", "market_label", "player_name", "side", "line_value",
    "model_pct", "edge_pp", "american_odds", "book", "kelly_units",
]


def write_outputs(
    output_dir: Path,
    target_date: str,
    all_rows: List[Dict[str, Any]],
    todays_card: List[Dict[str, Any]],
    thresholds: Dict[str, Any],
    n_lines_fetched: int,
    n_projected: int,
    n_skipped_low_conf: int,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "generated_at": generated_at,
        "date": target_date,
        "thresholds": thresholds,
        "counts": {
            "lines_fetched": n_lines_fetched,
            "projected": n_projected,
            "skipped_low_confidence": n_skipped_low_conf,
            "all_picks": len(all_rows),
            "todays_card": len(todays_card),
        },
        "all_picks": all_rows,
        "todays_card": todays_card,
    }
    (output_dir / "props_daily.json").write_text(
        json.dumps(payload, indent=2, default=str),
    )
    (output_dir / "todays_card.json").write_text(
        json.dumps({
            "generated_at": generated_at,
            "date": target_date,
            "thresholds": thresholds,
            "picks": todays_card,
        }, indent=2, default=str),
    )
    csv_path = output_dir / "todays_card.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in todays_card:
            writer.writerow(r)
    return {
        "json": output_dir / "props_daily.json",
        "todays_card_json": output_dir / "todays_card.json",
        "csv": csv_path,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edge_equation.exporters.props.daily",
        description="Build today's player-props card and dump JSON + CSV.",
    )
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-tier", default="STRONG",
                        choices=[t.value for t in Tier])
    parser.add_argument("--min-conviction", type=float, default=0.55,
                        help="Calibrated model_prob floor (0..1).")
    parser.add_argument("--min-edge-pp", type=float, default=5.0,
                        help="Minimum edge in percentage points.")
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--no-persist", action="store_true",
                        help="Skip writing predictions to DuckDB.")
    parser.add_argument("--no-flag-check", action="store_true",
                        help="Run even when EDGE_FEATURE_PROPS_PIPELINE is off.")
    args = parser.parse_args(argv)

    if not args.no_flag_check and not _flag_on():
        print(
            f"[props-daily] feature flag {FEATURE_FLAG_ENV}=off -- skipping. "
            f"Set {FEATURE_FLAG_ENV}=on to run.",
            file=sys.stderr,
        )
        return 0

    target_date = args.date or _today_utc()
    print(f"Props Daily -- target {target_date}")

    card = build_props_card(
        target_date=target_date,
        persist=not args.no_persist,
        top_n=args.top_n,
    )
    print(f"  lines fetched           {card.n_lines_fetched}")
    print(f"  projected               {card.n_projected}")
    print(f"  skipped (pure-prior)    {card.n_skipped_low_confidence}")
    print(f"  LEAN+ qualifying picks  {card.n_qualifying_picks}")

    all_rows = [_pick_to_dict(p) for p in card.picks]
    min_tier = Tier(args.min_tier)
    todays_card = _premium_filter(
        all_rows,
        min_tier=min_tier,
        min_conviction=args.min_conviction,
        min_edge_pp=args.min_edge_pp,
    )
    print(f"  todays_card             {len(todays_card)} pick(s) "
          f"(tier>={min_tier.value}, conviction>={args.min_conviction:.0%}, "
          f"edge>={args.min_edge_pp:.1f}pp)")

    thresholds = {
        "min_tier":       min_tier.value,
        "min_conviction": args.min_conviction,
        "min_edge_pp":    args.min_edge_pp,
    }
    written = write_outputs(
        output_dir=args.output_dir,
        target_date=target_date,
        all_rows=all_rows,
        todays_card=todays_card,
        thresholds=thresholds,
        n_lines_fetched=card.n_lines_fetched,
        n_projected=card.n_projected,
        n_skipped_low_conf=card.n_skipped_low_confidence,
    )
    for kind, path in written.items():
        print(f"  wrote {kind:<18} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
