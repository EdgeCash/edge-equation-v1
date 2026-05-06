"""Deterministic WNBA baseline projector.

Stdlib-only fallback used when the ML bundle isn't loaded (no model files
on disk yet, or the import fails). Math is intentionally lean:

  - Points  ~ usage * possessions * shot_quality
  - Rebounds ~ rebound_chance * minutes
  - Assists ~ assist_rate * possessions
  - Threes  ~ attempt_rate * minutes * accuracy
  - Full-game scores ~ team_ppp * possessions per side
  - ML/spread/totals ~ Bradley-Terry-ish from projected scores

Probabilities are bounded sigmoids of (proj - line) over a market-typical
spread. The constants are tuned to keep Brier under 0.25 in a thin
synthetic backtest; production tuning happens via WNBABacktester.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

from ..schema import EngineConfig, Market, Output


# Per-market spread used to convert (projection - line) into P(over).
# Empirically wider for compound markets (PRA) and tighter for booleans.
_PROB_SPREAD: Dict[Market, float] = {
    Market.POINTS:           5.5,
    Market.REBOUNDS:         3.0,
    Market.ASSISTS:          2.5,
    Market.PRA:              7.0,
    Market.THREES:           1.6,
    Market.FULLGAME_TOTAL:   12.0,
    Market.FULLGAME_ML:      8.0,
    Market.FULLGAME_SPREAD:  10.0,
}


def _logistic(x: float, scale: float) -> float:
    """Numerically stable logistic with a configurable scale."""
    if scale <= 0:
        return 0.5
    z = max(-30.0, min(30.0, x / scale))
    return 1.0 / (1.0 + math.exp(-z))


def _grade(edge_pct: float) -> str:
    if edge_pct >= 10:
        return "A+"
    if edge_pct >= 7:
        return "A"
    if edge_pct >= 5:
        return "A-"
    if edge_pct >= 3:
        return "B+"
    if edge_pct >= 1:
        return "B"
    return "C"


@dataclass
class DeterministicWNBA:
    config: EngineConfig

    # ------------------------------------------------------------------
    # Per-market projection helpers
    # ------------------------------------------------------------------

    def project_points(
        self,
        usage: float,
        possessions: float,
        shot_quality: float,
    ) -> float:
        return usage * possessions * shot_quality * self.config.usage_weight

    def project_rebounds(self, rebound_chance: float, minutes: float) -> float:
        return rebound_chance * minutes * self.config.rebound_weight

    def project_assists(self, assist_rate: float, possessions: float) -> float:
        return assist_rate * possessions * self.config.assist_weight

    def project_threes(
        self, attempt_rate: float, minutes: float, accuracy: float,
    ) -> float:
        return attempt_rate * minutes * accuracy * self.config.three_weight

    def project_fullgame_scores(
        self, team_ppp: float, opp_ppp: float, possessions: float,
    ) -> Dict[str, float]:
        return {
            "team_score": team_ppp * possessions * self.config.efficiency_weight,
            "opp_score":  opp_ppp  * possessions * self.config.efficiency_weight,
        }

    # ------------------------------------------------------------------
    # Per-market probability helpers
    # ------------------------------------------------------------------

    def prob_over(self, market: Market, projection: float, line: float) -> float:
        spread = _PROB_SPREAD.get(market, 4.0)
        return _logistic(projection - line, spread / 2.0)

    def prob_ml(self, team_score: float, opp_score: float) -> float:
        spread = _PROB_SPREAD[Market.FULLGAME_ML]
        return _logistic(team_score - opp_score, spread / 2.0)

    # ------------------------------------------------------------------
    # Output assembly
    # ------------------------------------------------------------------

    def build_output(
        self,
        market: Market,
        player: Optional[str],
        team: Optional[str],
        opponent: Optional[str],
        projection: float,
        line: float,
        probability: float,
        team_score: Optional[float] = None,
        opp_score: Optional[float] = None,
        explanation: Optional[str] = None,
    ) -> Output:
        edge = projection - line
        edge_pct = (edge / line * 100.0) if line else edge
        confidence = abs(probability - 0.5) * 2.0
        return Output(
            market=market,
            player=player,
            team=team,
            opponent=opponent,
            projection=projection,
            line=line,
            probability=probability,
            edge=edge,
            confidence=confidence,
            grade=_grade(edge_pct),
            team_score=team_score,
            opp_score=opp_score,
            explanation=explanation,
            model_version="deterministic_v1",
        )

    def _grade(self, edge: float) -> str:  # backwards-compat for ml_bundle
        return _grade(edge)
