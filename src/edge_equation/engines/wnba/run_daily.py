"""Daily WNBA engine runner.

End-to-end pipeline:
  1. Pull today's slate + offered props from `ingestion.wnba_source`.
  2. Build features per offer.
  3. Score with the ML bundle (auto-falls-through to the deterministic
     baseline when no model file is on disk).
  4. Apply the BRAND_GUIDE quality gate (`Output.is_qualified`) so only
     plays clearing the edge / conviction floor survive.
  5. Persist `engine_picks.json` next to the exporter's outputs so the
     premium combined spreadsheet picks them up.

Usage::

    python -m edge_equation.engines.wnba.run_daily
    python -m edge_equation.engines.wnba.run_daily --date 2026-05-09
    python -m edge_equation.engines.wnba.run_daily --output-dir /tmp/test
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from edge_equation.ingestion.wnba_source import (
    get_daily_games, get_player_props,
)
from edge_equation.utils.logging import get_logger

from .model_bundle.deterministic import DeterministicWNBA
from .model_bundle.ml_bundle import WNBAMLBundle
from .schema import EngineConfig, Market, Output


log = get_logger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "website" / "public" / "data" / "wnba"


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class WNBARunner:
    """Daily slate runner. No SlateRunner inheritance -- the v1 base
    class is mid-port and pulling it in just to satisfy a parent that
    we don't use here would add a transient broken import."""
    config: EngineConfig = field(default_factory=EngineConfig)

    def __post_init__(self) -> None:
        self.det = DeterministicWNBA(self.config)
        self.ml = WNBAMLBundle(self.det)

    # ------------------------------------------------------------------

    def run(self, date: Optional[str] = None) -> List[Output]:
        target = date or _today_utc()
        log.info("WNBA engine running for %s", target)

        games = get_daily_games(target)
        props = get_player_props(target)
        log.info(
            "Fetched %d game(s) and %d offer(s) for %s",
            len(games), len(props), target,
        )

        outputs: List[Output] = []
        for prop in props:
            try:
                market = Market(prop["market_type"])
            except (ValueError, KeyError):
                continue
            features = self._build_features(prop)
            meta = {
                "player": prop.get("player"),
                "team": prop.get("team"),
                "opponent": prop.get("opponent"),
                "game_time": prop.get("game_time"),
                "decimal_odds": prop.get("decimal_odds"),
                "book": prop.get("book"),
            }
            try:
                line = float(prop.get("line", 0))
            except (TypeError, ValueError):
                line = 0.0
            try:
                out = self.ml.predict(market, features, line, meta)
            except Exception as e:
                log.debug(
                    "WNBA bundle predict failed (%s: %s) -- skipping row",
                    type(e).__name__, e,
                )
                continue
            out.meta = meta
            if out.is_qualified(
                min_edge_pct=self.config.min_edge_pct,
                min_conviction=self.config.min_conviction,
            ):
                outputs.append(out)
        log.info("WNBA run complete -- %d qualified output(s)", len(outputs))
        return outputs

    # ------------------------------------------------------------------

    def _build_features(self, prop: Dict[str, Any]) -> Dict[str, float]:
        """Coerce to floats with sane fallbacks. Missing fields stay
        at the deterministic engine's neutral defaults so we never
        explode on a thin WNBA slate row."""
        def _f(key: str, default: float) -> float:
            try:
                return float(prop.get(key, default))
            except (TypeError, ValueError):
                return default
        return {
            "usage":          _f("usage", 0.20),
            "possessions":    _f("possessions", 70.0),
            "shot_quality":   _f("shot_quality", 1.0),
            "rebound_chance": _f("rebound_chance", 0.10),
            "minutes":        _f("minutes", 30.0),
            "assist_rate":    _f("assist_rate", 0.05),
            "three_rate":     _f("three_rate", 0.10),
            "three_accuracy": _f("three_accuracy", 0.35),
            "team_ppp":       _f("team_ppp", 1.0),
            "opp_ppp":        _f("opp_ppp", 1.0),
        }


# ---------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edge_equation.engines.wnba.run_daily",
        description="Run the WNBA engine and dump engine_picks.json.",
    )
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-edge-pct", type=float, default=3.0)
    parser.add_argument("--min-conviction", type=float, default=0.55)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results to stdout without writing JSON.")
    args = parser.parse_args(argv)

    config = EngineConfig(
        min_edge_pct=args.min_edge_pct,
        min_conviction=args.min_conviction,
    )
    runner = WNBARunner(config=config)
    outputs = runner.run(args.date)

    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": args.date or _today_utc(),
        "n_qualified": len(outputs),
        "min_edge_pct": args.min_edge_pct,
        "min_conviction": args.min_conviction,
        "picks": [o.to_dict() for o in outputs],
    }

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "engine_picks.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(
        f"WNBA engine wrote {len(outputs)} qualified pick(s) to {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
