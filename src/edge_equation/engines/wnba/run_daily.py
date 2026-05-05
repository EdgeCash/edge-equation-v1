from typing import List, Dict, Any
from dataclasses import dataclass
from edge_equation.engine.slate_runner import SlateRunner
from edge_equation.ingestion.wnba import get_daily_games, get_player_props
from edge_equation.utils.logger import log
from edge_equation.compliance.sanitizer import sanitize_output
from edge_equation.publishing.formatter import format_for_spreadsheet
from .schema import Market, EngineConfig, Output
from .model_bundle.deterministic import DeterministicWNBA
from .model_bundle.ml_bundle import WNBAMLBundle


@dataclass
class WNBARunner(SlateRunner):
    """
    Daily WNBA slate runner.
    Ready for backtesting and daily projections.
    """
    config: EngineConfig = EngineConfig()

    def __post_init__(self):
        self.det = DeterministicWNBA(self.config)
        self.ml = WNBAMLBundle(self.det)

    def run(self, date: str = None) -> List[Output]:
        log.info(f"WNBA engine running for date: {date or 'today'}")

        games = get_daily_games(date)
        props = get_player_props(date)

        log.info(f"Fetched {len(games)} games and {len(props)} props")

        outputs: List[Output] = []

        for prop in props:
            try:
                market = Market(prop["market_type"])
            except Exception:
                continue  # skip unsupported markets for now

            features = self._build_features(prop, games)
            meta = {
                "player": prop.get("player"),
                "team": prop.get("team"),
                "opponent": prop.get("opponent"),
                "game_time": prop.get("game_time"),
            }

            line = float(prop.get("line", 0))
            
            # ML → Deterministic fallback
            out = self.ml.predict(
                market=market,
                features=features,
                line=line,
                meta=meta,
            )

            # Apply brand compliance + quality gate
            sanitized = sanitize_output(out)
            if sanitized.is_qualified():   # respects your +1% ROI / Brier gate
                outputs.append(sanitized)

        log.info(f"WNBA run complete — {len(outputs)} qualified outputs")
        return outputs

    def _build_features(self, prop: Dict[str, Any], games: List[Dict[str, Any]]) -> Dict[str, float]:
        """Build features for WNBA player props"""
        return {
            "usage": float(prop.get("usage", 0.0)),
            "minutes_proj": float(prop.get("minutes", 28.0)),
            "opp_def_rating": float(prop.get("opp_def_rating", 100.0)),
            "pace": float(prop.get("pace", 95.0)),
            "rest_days": float(prop.get("rest_days", 2.0)),
            "home_away": 1.0 if prop.get("is_home", False) else 0.0,
            "three_rate": float(prop.get("three_rate", 0.0)),
            "shot_quality": float(prop.get("shot_quality", 1.0)),
        }


# ---------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------
def main():
    runner = WNBARunner()
    outputs = runner.run()
    
    for o in outputs:
        print(o.to_dict())
    
    # Optional: save for spreadsheet
    # from edge_equation.exporters import save_wnba_outputs
    # save_wnba_outputs(outputs)

if __name__ == "__main__":
    main()
