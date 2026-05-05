import argparse
from typing import List, Dict, Any
from dataclasses import dataclass

from edge_equation.utils.logger import log
from edge_equation.ingestion.wnba import get_historical_props
from .schema import Market, EngineConfig
from .model_bundle.deterministic import DeterministicWNBA
from .model_bundle.ml_bundle import WNBAMLBundle


@dataclass
class WNBABacktester:
    """
    Historical backtester for WNBA props + full-game markets.
    Mirrors the NRFI backtest structure but simplified.
    """

    config: EngineConfig = EngineConfig()

    def __post_init__(self):
        self.det = DeterministicWNBA(self.config)
        self.ml = WNBAMLBundle(self.det)

    # ---------------------------------------------------------
    # Main entry point
    # ---------------------------------------------------------

    def run(self, start: str, end: str, use_model: bool = True) -> Dict[str, Any]:
        log.info(f"WNBA backtest: {start} → {end}")

        rows = get_historical_props(start, end)
        results = []

        for row in rows:
            try:
                market = Market(row["market"])
            except Exception:
                continue

            features = self._build_features(row)
            meta = {
                "player": row["player"],
                "team": row["team"],
                "opponent": row["opponent"],
            }

            line = float(row["line"])
            actual = float(row["actual"])

            if use_model:
                out = self.ml.predict(market, features, line, meta)
            else:
                out = self.det.build_output(
                    market=market,
                    player=row["player"],
                    team=row["team"],
                    opponent=row["opponent"],
                    projection=features.get("projection", 0.0),
                    line=line,
                    probability=0.5,
                    edge=features.get("projection", 0.0) - line,
                    confidence=abs(features.get("projection", 0.0) - line),
                )

            hit = 1 if actual > line else 0

            results.append({
                "market": market.value,
                "player": row["player"],
                "projection": out.projection,
                "line": line,
                "actual": actual,
                "edge": out.edge,
                "hit": hit,
            })

        return self._summarize(results)

    # ---------------------------------------------------------
    # Feature builder (same as run_daily)
    # ---------------------------------------------------------

    def _build_features(self, row: Dict[str, Any]) -> Dict[str, float]:
        return {
            "usage": row.get("usage", 0.0),
            "possessions": row.get("possessions", 70.0),
            "shot_quality": row.get("shot_quality", 1.0),
            "rebound_chance": row.get("rebound_chance", 0.0),
            "assist_rate": row.get("assist_rate", 0.0),
            "minutes": row.get("minutes", 28.0),
            "three_rate": row.get("three_rate", 0.0),
            "three_accuracy": row.get("three_accuracy", 0.0),
            "team_ppp": row.get("team_ppp", 1.0),
            "opp_ppp": row.get("opp_ppp", 1.0),
        }

    # ---------------------------------------------------------
    # Summary
    # ---------------------------------------------------------

    def _summarize(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {"count": 0, "roi": 0.0, "hit_rate": 0.0}

        count = len(rows)
        hits = sum(r["hit"] for r in rows)
        hit_rate = hits / count

        # Simple ROI: +1 for hit, -1 for miss
        roi = (hits - (count - hits)) / count

        return {
            "count": count,
            "hit_rate": round(hit_rate, 3),
            "roi": round(roi, 3),
        }


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="WNBA historical backtest")
    parser.add_argument("start", type=str)
    parser.add_argument("end", type=str)
    parser.add_argument("--use-model", action="store_true")

    args = parser.parse_args()

    bt = WNBABacktester()
    summary = bt.run(args.start, args.end, use_model=args.use_model)
    print(summary)


if __name__ == "__main__":
    main()
