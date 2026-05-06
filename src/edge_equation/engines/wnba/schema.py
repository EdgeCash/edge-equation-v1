"""WNBA engine canonical types.

Kept stdlib-only so the runner can import on a fresh checkout without
pulling numpy or sklearn until the ML bundle actually fires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Market(Enum):
    POINTS = "points"
    REBOUNDS = "rebounds"
    ASSISTS = "assists"
    PRA = "pra"
    THREES = "3pm"
    FULLGAME_TOTAL = "fullgame_total"
    FULLGAME_ML = "fullgame_ml"
    FULLGAME_SPREAD = "fullgame_spread"


@dataclass
class EngineConfig:
    """Tuning knobs for the deterministic projector."""
    pace_weight: float = 1.0
    usage_weight: float = 1.0
    efficiency_weight: float = 1.0
    rebound_weight: float = 1.0
    assist_weight: float = 1.0
    three_weight: float = 1.0
    ml_weight: float = 1.0
    total_weight: float = 1.0

    # BRAND_GUIDE-aligned quality gate. Slightly looser Brier than MLB
    # since basketball lines are noisier.
    min_edge_pct: float = 3.0
    min_conviction: float = 0.55
    max_brier_for_publish: float = 0.250


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

    shap: Optional[Dict[str, float]] = None
    model_version: Optional[str] = None

    team_score: Optional[float] = None
    opp_score: Optional[float] = None

    explanation: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def is_qualified(
        self,
        min_edge_pct: float = 3.0,
        min_conviction: float = 0.55,
    ) -> bool:
        """Single source of truth for the WNBA quality gate. Mirrors
        the MLB exporter's PLAY/PASS contract: a row is shippable when
        the edge clears the per-market floor AND the model's conviction
        clears the no-coin-flip floor.

        For points-style markets `edge` is in points (proj minus line).
        We translate to a percentage by `edge / max(line, 1)` so the
        same threshold is meaningful across markets.
        """
        if self.line and self.line > 0:
            edge_pct = (self.edge / self.line) * 100.0
        else:
            edge_pct = self.edge
        return (edge_pct >= min_edge_pct
                and self.probability >= min_conviction)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market.value,
            "player": self.player,
            "team": self.team,
            "opponent": self.opponent,
            "projection": round(self.projection, 3),
            "line": self.line,
            "edge": round(self.edge, 3),
            "probability": round(self.probability, 4),
            "confidence": round(self.confidence, 3),
            "grade": self.grade,
            "shap": self.shap,
            "model_version": self.model_version,
            "team_score": self.team_score,
            "opp_score": self.opp_score,
            "explanation": self.explanation,
            "meta": self.meta,
        }
