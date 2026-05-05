from typing import List, Dict, Any
from dataclasses import dataclass

from edge_equation.engine.slate import SlateRunner
from edge_equation.ingestion.wnba import get_daily_games, get_player_props
from edge_equation.utils.logger import log

from .schema import Market, EngineConfig
from .model_bundle.deterministic import DeterministicWNBA
from .model_bundle.ml_bundle import WNBAMLBundle


@dataclass
class WNBARunner:
    """
    Daily WNBA slate runner.
    - Pulls games + props
    - Builds features
    - Calls ML → deterministic fallback
    - Returns list of Output objects
    """

    config: EngineConfig = EngineConfig()

    def __post_init__(self):
        self.det = DeterministicWNBA(self.config)
        self.ml = WNBAMLBundle(self.det)

    # ---------------------------------------------------------
    # Main entry point
    # ---------------------------------------------------------

    def run(self, date: str = None) -> List[Any]:
        log.info("WNBA engine: fetching slate...")
        games = get_daily_games(date)
        props = get_player_props(date)

        outputs = []

        for prop in props:
            try:
                market = Market(prop["market"])
            except Exception:
                continue  # skip unsupported markets

            features = self._build_features(prop, games)
            meta = {
                "player": prop["player"],
                "team": prop["team"],
                "opponent": prop["opponent"],
            }

            line = float(prop["line"])

            out = self.ml.predict(
                market=market,
                features=features,
                line=line,
                meta=meta,
            )

            outputs.append(out)

        return outputs

    # ---------------------------------------------------------
    # Feature builder (deterministic v1)
    # ---------------------------------------------------------

    def _build_features(self, prop: Dict[str, Any], games: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Deterministic feature builder.
        ML bundle will use these features directly.
        """

        # These fields come from your ingestion layer
        return {
            "usage": prop.get("usage", 0.0),
            "possessions": prop.get("possessions", 70.0),
            "shot_quality": prop.get("shot_quality", 1.0),
            "rebound_chance": prop.get("rebound_chance", 0.0),
            "assist_rate": prop.get("assist_rate", 0.0),
            "minutes": prop.get("minutes", 28.0),
            "three_rate": prop.get("three_rate", 0.0),
            "three_accuracy": prop.get("three_accuracy", 0.0),
            "team_ppp": prop.get("team_ppp", 1.0),
            "opp_ppp": prop.get("opp_ppp", 1.0),
        }


# ---------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------

def main():
    runner = WNBARunner()
    outputs = runner.run()
    for o in outputs:
        print(o.to_dict())


if __name__ == "__main__":
    main()
