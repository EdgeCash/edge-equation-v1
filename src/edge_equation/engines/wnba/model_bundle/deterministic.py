Here is **Step 2** — the deterministic engine for WNBA.  
This file is intentionally **lean, safe, and iPad‑friendly**, and it plugs directly into your math primitives without requiring any ML yet.

Paste this into:

`src/edge_equation/engines/wnba/model_bundle/deterministic.py`

---

# ⭐ **WNBA — Step 2: `deterministic.py`**  
Paste exactly as-is:

```python
from dataclasses import dataclass
from typing import Dict, Any
from ..schema import Output, Market, EngineConfig

# These imports already exist in your deterministic core
from edge_equation.math.poisson import poisson_mean
from edge_equation.math.bradley_terry import win_probability
from edge_equation.math.monte_carlo import simulate_distribution


@dataclass
class DeterministicWNBA:
    """
    Deterministic baseline for WNBA projections.
    This is the fallback when no ML bundle is available.
    """

    config: EngineConfig

    def project_points(self, usage: float, possessions: float, shot_quality: float) -> float:
        return usage * possessions * shot_quality

    def project_rebounds(self, rebound_chance: float, minutes: float) -> float:
        return rebound_chance * minutes

    def project_assists(self, assist_rate: float, possessions: float) -> float:
        return assist_rate * possessions

    def project_threes(self, attempt_rate: float, minutes: float, accuracy: float) -> float:
        return attempt_rate * minutes * accuracy

    def project_fullgame_scores(self, team_ppp: float, opp_ppp: float, possessions: float) -> Dict[str, float]:
        team_score = team_ppp * possessions
        opp_score = opp_ppp * possessions
        return {"team_score": team_score, "opp_score": opp_score}

    def project_fullgame_ml(self, team_score: float, opp_score: float) -> float:
        return win_probability(team_score, opp_score)

    def project_total_probability(self, team_score: float, opp_score: float, line: float) -> float:
        # Poisson-based scoring distribution
        mean_total = team_score + opp_score
        return poisson_mean(mean_total, line)

    def build_output(
        self,
        market: Market,
        player: str,
        team: str,
        opponent: str,
        projection: float,
        line: float,
        probability: float,
        edge: float,
        confidence: float,
        team_score: float = None,
        opp_score: float = None,
    ) -> Output:

        grade = self._grade(edge)

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
            grade=grade,
            team_score=team_score,
            opp_score=opp_score,
            model_version="deterministic_v1",
        )

    def _grade(self, edge: float) -> str:
        if edge >= 8:
            return "A+"
        if edge >= 5:
            return "A"
        if edge >= 3:
            return "A-"
        if edge >= 1:
            return "B+"
        return "C"
