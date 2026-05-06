"""Historical WNBA backtest.

Walks the on-disk backfill (`data/backfill/wnba/<season>/games.json`),
shapes each game into one row per market (ML / Total / Spread) via
``ingestion.wnba_source.get_historical_props``, scores each row against
the deterministic baseline, and aggregates ROI / hit-rate / Brier per
market plus an overall summary.

Mirrors the MLB BacktestEngine pattern so the Premium combined exporter
can read either source's `summary_by_bet_type` block without per-sport
branching.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from edge_equation.ingestion.wnba_source import get_historical_props
from edge_equation.utils.logging import get_logger

from .model_bundle.deterministic import DeterministicWNBA
from .model_bundle.ml_bundle import WNBAMLBundle
from .schema import EngineConfig, Market


log = get_logger(__name__)


_FLAT_DECIMAL = 1.909  # -110, the assumed historical price


def _settle(decimal_odds: float, won: bool, push: bool) -> float:
    if push:
        return 0.0
    if won:
        return round(decimal_odds - 1.0, 4)
    return -1.0


@dataclass
class WNBABacktester:
    config: EngineConfig = field(default_factory=EngineConfig)

    def __post_init__(self) -> None:
        self.det = DeterministicWNBA(self.config)
        self.ml = WNBAMLBundle(self.det)

    # ------------------------------------------------------------------

    def run(
        self,
        start: str,
        end: str,
        use_model: bool = True,
        decimal_odds: float = _FLAT_DECIMAL,
    ) -> Dict[str, Any]:
        log.info("WNBA backtest %s -> %s", start, end)
        rows = get_historical_props(start, end)
        if not rows:
            log.info("No historical rows in window -- empty summary.")
            return self._empty_summary(start, end)

        bets: List[Dict[str, Any]] = []
        for row in rows:
            try:
                market = Market(row["market"])
            except (ValueError, KeyError):
                continue
            line = float(row.get("line", 0.0))
            actual = float(row.get("actual", 0.0))
            features = self._features(row)
            meta = {
                "player": row.get("player"),
                "team": row.get("team"),
                "opponent": row.get("opponent"),
            }
            if use_model:
                out = self.ml.predict(market, features, line, meta)
            else:
                proj = self.det.project_points(
                    features["usage"], features["possessions"],
                    features["shot_quality"],
                )
                out = self.det.build_output(
                    market=market, player=row.get("player"),
                    team=row.get("team"), opponent=row.get("opponent"),
                    projection=proj, line=line,
                    probability=self.det.prob_over(market, proj, line),
                )
            won, push = self._grade(market, out.projection, line, actual)
            prob = out.probability
            bets.append({
                "date": row.get("date"),
                "matchup": row.get("player"),
                "bet_type": market.value,
                "pick": "OVER" if out.projection >= line else "UNDER",
                "model_prob": round(prob, 4),
                "result": "PUSH" if push else ("WIN" if won else "LOSS"),
                "units": _settle(decimal_odds, won, push),
            })

        return {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "start": start, "end": end,
            "summary_by_bet_type": self._summarize(bets),
            "overall": self._overall(bets),
            "bets": bets,
        }

    # ------------------------------------------------------------------

    @staticmethod
    def _grade(market: Market, projection: float, line: float,
               actual: float) -> tuple[bool, bool]:
        # PUSH possible only on whole-number total/spread lines.
        push = (math.floor(line) == line and actual == line and
                market in (Market.FULLGAME_TOTAL, Market.FULLGAME_SPREAD))
        if market == Market.FULLGAME_ML:
            won = (projection >= 0.5 and actual >= 0.5) or \
                  (projection < 0.5 and actual < 0.5)
        else:
            picked_over = projection >= line
            won = (actual > line) if picked_over else (actual < line)
        return won, push

    def _features(self, row: Dict[str, Any]) -> Dict[str, float]:
        def _f(k: str, d: float) -> float:
            try:
                return float(row.get(k, d))
            except (TypeError, ValueError):
                return d
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

    @staticmethod
    def _brier(bets: List[Dict[str, Any]]) -> Optional[float]:
        scored = [
            (b["model_prob"], 1 if b["result"] == "WIN" else 0)
            for b in bets if b["result"] in ("WIN", "LOSS")
            and b.get("model_prob") is not None
        ]
        if not scored:
            return None
        return round(sum((p - y) ** 2 for p, y in scored) / len(scored), 4)

    @classmethod
    def _summarize(cls, bets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        from collections import defaultdict
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for b in bets:
            groups[b["bet_type"]].append(b)
        rows = []
        for bet_type in sorted(groups):
            grp = groups[bet_type]
            wins = sum(1 for b in grp if b["result"] == "WIN")
            losses = sum(1 for b in grp if b["result"] == "LOSS")
            pushes = sum(1 for b in grp if b["result"] == "PUSH")
            graded = wins + losses
            units = round(sum(b["units"] for b in grp), 2)
            rows.append({
                "bet_type": bet_type,
                "bets": len(grp),
                "wins": wins,
                "losses": losses,
                "pushes": pushes,
                "hit_rate": round(wins / graded * 100, 1) if graded else 0.0,
                "units_pl": units,
                "roi_pct": round(units / len(grp) * 100, 2) if grp else 0.0,
                "brier": cls._brier(grp),
            })
        return rows

    @classmethod
    def _overall(cls, bets: List[Dict[str, Any]]) -> Dict[str, Any]:
        wins = sum(1 for b in bets if b["result"] == "WIN")
        losses = sum(1 for b in bets if b["result"] == "LOSS")
        pushes = sum(1 for b in bets if b["result"] == "PUSH")
        graded = wins + losses
        units = round(sum(b["units"] for b in bets), 2)
        return {
            "bets": len(bets),
            "wins": wins, "losses": losses, "pushes": pushes,
            "hit_rate": round(wins / graded * 100, 1) if graded else 0.0,
            "units_pl": units,
            "roi_pct": round(units / len(bets) * 100, 2) if bets else 0.0,
            "brier": cls._brier(bets),
        }

    @staticmethod
    def _empty_summary(start: str, end: str) -> Dict[str, Any]:
        return {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "start": start, "end": end,
            "summary_by_bet_type": [],
            "overall": {"bets": 0, "wins": 0, "losses": 0, "pushes": 0,
                        "hit_rate": 0.0, "units_pl": 0.0, "roi_pct": 0.0,
                        "brier": None},
            "bets": [],
        }


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edge_equation.engines.wnba.backtest_historical",
        description="Replay the WNBA backfill through the engine.",
    )
    parser.add_argument("start", help="YYYY-MM-DD")
    parser.add_argument("end", help="YYYY-MM-DD")
    parser.add_argument("--use-model", action="store_true",
                        help="Use ML bundle (auto-falls-through to "
                             "deterministic if no model files present).")
    parser.add_argument("--output", type=Path, default=None,
                        help="Optional path to write the full summary JSON.")
    args = parser.parse_args(argv)

    bt = WNBABacktester()
    summary = bt.run(args.start, args.end, use_model=args.use_model)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2))
        print(f"Wrote summary to {args.output}")
    print(json.dumps({
        "overall": summary["overall"],
        "by_bet_type": summary["summary_by_bet_type"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
