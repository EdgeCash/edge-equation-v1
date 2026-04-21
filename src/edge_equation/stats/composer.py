"""
FeatureComposer: stitches Elo ratings + rolling team stats into the feature
input dicts the ProbabilityCalculator consumes.

For ML markets:
  strength_home / strength_away are translated from Elo ratings using the
  Bradley-Terry-compatible form:
      strength = exp((rating - 1500) / 400)
  The ProbabilityCalculator's bradley_terry(home, away, home_adv) then
  recovers the same win probability Elo assigns.

For Total markets:
  off_env / def_env / pace come from TeamStats.matchup_factors.
  dixon_coles_adj is left at 0.0 here (no systematic low-score bias
  estimate yet; that lives in math.rho for future training).

The composer is pure: same (home, away, league, results) in -> same dict
out. No DB access; the caller supplies the results list.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional
import math

from edge_equation.stats.elo import EloCalculator, EloRatings, STARTING_RATING
from edge_equation.stats.results import GameResult
from edge_equation.stats.team_stats import MatchupFactors, TeamStats


@dataclass(frozen=True)
class ComposedFeatures:
    """All four flavors of feature-input a caller might want for one matchup."""
    ml_inputs: Dict[str, float]
    totals_inputs: Dict[str, float]
    elo: EloRatings
    matchup: MatchupFactors

    def to_dict(self) -> dict:
        return {
            "ml_inputs": dict(self.ml_inputs),
            "totals_inputs": dict(self.totals_inputs),
            "elo": self.elo.to_dict(),
            "matchup": self.matchup.to_dict(),
        }


class FeatureComposer:
    """
    Glue between raw game results and engine feature inputs:
    - compose(home, away, league, results) -> ComposedFeatures
    - rating_to_strength(rating)            -> float (for Bradley-Terry)
    """

    @staticmethod
    def rating_to_strength(rating: Decimal) -> float:
        """
        Map an Elo rating to the multiplicative "strength" the engine's
        Bradley-Terry model expects. Strength at 1500 Elo == 1.0, so a team
        200 Elo points above average has strength ~= e^0.5 = 1.648.
        """
        return math.exp((float(rating) - float(STARTING_RATING)) / 400.0)

    @staticmethod
    def compose(
        home: str,
        away: str,
        league: str,
        results: List[GameResult],
        elo: Optional[EloRatings] = None,
    ) -> ComposedFeatures:
        scoped = [r for r in results if r.league == league]
        if elo is None:
            elo = EloCalculator.replay(league, scoped)
        matchup = TeamStats.matchup_factors(home, away, league, scoped)

        home_rating = elo.rating_for(home)
        away_rating = elo.rating_for(away)
        strength_home = FeatureComposer.rating_to_strength(home_rating)
        strength_away = FeatureComposer.rating_to_strength(away_rating)

        ml_inputs = {
            "strength_home": float(strength_home),
            "strength_away": float(strength_away),
            # home_adv is left to the HFA module / context layer to populate;
            # we keep it at the engine's default here.
            "home_adv": 0.115,
        }
        totals_inputs = {
            "off_env": float(matchup.off_env),
            "def_env": float(matchup.def_env),
            "pace": float(matchup.pace),
            "dixon_coles_adj": 0.0,
        }
        return ComposedFeatures(
            ml_inputs=ml_inputs,
            totals_inputs=totals_inputs,
            elo=elo,
            matchup=matchup,
        )

    @staticmethod
    def enrich_markets(
        raw_markets: List[dict],
        raw_games: List[dict],
        league: str,
        results: List[GameResult],
    ) -> List[dict]:
        """
        Walk a list of raw_markets (the shape ingestion sources emit) and
        attach a meta['inputs'] + meta['universal_features'] block to every
        market whose game appears in raw_games. Existing meta['inputs'] values
        are preserved -- this fills gaps without overwriting.

        Returns a new list (inputs list is not mutated).
        """
        games_by_id = {g["game_id"]: g for g in raw_games if g.get("league") == league}
        elo = EloCalculator.replay(league, [r for r in results if r.league == league])
        enriched: List[dict] = []
        for market in raw_markets:
            gid = market.get("game_id")
            game = games_by_id.get(gid)
            if game is None:
                enriched.append(market)
                continue
            meta = dict(market.get("meta") or {})
            if meta.get("inputs") is not None:
                enriched.append(market)
                continue
            composed = FeatureComposer.compose(
                home=game["home_team"],
                away=game["away_team"],
                league=league,
                results=results,
                elo=elo,
            )
            market_type = market.get("market_type")
            if market_type in ("ML", "Run_Line", "Puck_Line", "Spread"):
                meta["inputs"] = dict(composed.ml_inputs)
            elif market_type in ("Total", "Game_Total"):
                meta["inputs"] = dict(composed.totals_inputs)
            else:
                enriched.append(market)
                continue
            meta.setdefault("universal_features", {})
            new_market = dict(market)
            new_market["meta"] = meta
            enriched.append(new_market)
        return enriched
