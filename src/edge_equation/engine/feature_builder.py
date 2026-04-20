"""
Feature builder.

Produces a FeatureBundle that the math layer can consume directly. Validates
sport + market_type against sport_config. Drops unknown universal feature keys.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from edge_equation.math.stats import DeterministicStats
from edge_equation.config.sport_config import SPORT_CONFIG


RATE_PROP_MARKETS = {
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
}
ML_MARKETS = {"ML", "Run_Line", "Puck_Line", "Spread"}
TOTAL_MARKETS = {"Total", "Game_Total"}
BTTS_MARKETS = {"BTTS"}
PASSTHROUGH_MARKETS = {"NRFI", "YRFI"}


@dataclass
class FeatureBundle:
    sport: str
    market_type: str
    inputs: dict
    universal_features: dict
    game_id: Optional[str] = None
    event_time: Optional[str] = None
    selection: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "market_type": self.market_type,
            "inputs": dict(self.inputs),
            "universal_features": dict(self.universal_features),
            "game_id": self.game_id,
            "event_time": self.event_time,
            "selection": self.selection,
            "metadata": dict(self.metadata),
        }


class FeatureBuilder:
    @staticmethod
    def _validate_sport_market(sport: str, market_type: str) -> None:
        if sport not in SPORT_CONFIG:
            raise ValueError(f"Unknown sport: {sport}")
        allowed = SPORT_CONFIG[sport]["markets"]
        if market_type not in allowed:
            raise ValueError(
                f"Market '{market_type}' not supported for sport '{sport}'. "
                f"Allowed: {allowed}"
            )

    @staticmethod
    def _normalize_universal(raw: dict) -> dict:
        clean = {}
        for k in DeterministicStats.UNIVERSAL_KEYS:
            if k in raw:
                clean[k] = float(raw[k])
        return clean

    @staticmethod
    def _validate_inputs(market_type: str, inputs: dict) -> None:
        if market_type in ML_MARKETS:
            required = ["strength_home", "strength_away"]
        elif market_type in TOTAL_MARKETS:
            required = ["off_env", "def_env", "pace"]
        elif market_type in RATE_PROP_MARKETS:
            required = ["rate"]
        elif market_type in BTTS_MARKETS:
            required = ["home_lambda", "away_lambda"]
        elif market_type in PASSTHROUGH_MARKETS:
            required = []
        else:
            raise ValueError(f"Unsupported market_type: {market_type}")
        missing = [k for k in required if k not in inputs]
        if missing:
            raise ValueError(
                f"Missing required inputs for market '{market_type}': {missing}"
            )

    @staticmethod
    def build(
        sport: str,
        market_type: str,
        inputs: dict,
        universal_features: Optional[dict] = None,
        game_id: Optional[str] = None,
        event_time: Optional[str] = None,
        selection: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> FeatureBundle:
        FeatureBuilder._validate_sport_market(sport, market_type)
        FeatureBuilder._validate_inputs(market_type, inputs)
        clean_univ = FeatureBuilder._normalize_universal(universal_features or {})
        return FeatureBundle(
            sport=sport,
            market_type=market_type,
            inputs=dict(inputs),
            universal_features=clean_univ,
            game_id=game_id,
            event_time=event_time,
            selection=selection,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def sport_weights(sport: str) -> dict:
        if sport not in SPORT_CONFIG:
            raise ValueError(f"Unknown sport: {sport}")
        cfg = SPORT_CONFIG[sport]
        return {
            "league_baseline_total": cfg["league_baseline_total"],
            "ml_universal_weight": cfg["ml_universal_weight"],
            "prop_universal_weight": cfg["prop_universal_weight"],
        }
