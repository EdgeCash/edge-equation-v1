from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any


class Market(Enum):
    POINTS = "points"
    REBOUNDS = "rebounds"
    ASSISTS = "assists"
    PRA = "pra"
    THREES = "3pm"
    FULLGAME_TOTAL = "fullgame_total"
    FULLGAME_ML = "fullgame_ml"


@dataclass
class Output:
    market: Market
    player: Optional[str] = None
    team: Optional[str] = None
    opponent: Optional[str] = None

    projection: float = 0.0
    line: float = 0.0
    edge: float = 0.0
    probability: float = 0.0
    confidence: float = 0.0
    grade: str = "N/A"

    # Optional ML extras
    shap: Optional[Dict[str, float]] = None
    model_version: Optional[str] = None

    # For full-game markets
    team_score: Optional[float] = None
    opp_score: Optional[float] = None

    # For posting / dashboard
    explanation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market.value,
            "player": self.player,
            "team": self.team,
            "opponent": self.opponent,
            "projection": self.projection,
            "line": self.line,
            "edge": self.edge,
            "probability": self.probability,
            "confidence": self.confidence,
            "grade": self.grade,
            "shap": self.shap,
            "model_version": self.model_version,
            "team_score": self.team_score,
            "opp_score": self.opp_score,
            "explanation": self.explanation,
        }


@dataclass
class EngineConfig:
    # Placeholder for tuning knobs (pace multipliers, decay, etc.)
    pace_weight: float = 1.0
    usage_weight: float = 1.0
    efficiency_weight: float = 1.0
    rebound_weight: float = 1.0
    assist_weight: float = 1.0
    three_weight: float = 1.0
    ml_weight: float = 1.0
    total_weight: float = 1.0
