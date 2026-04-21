"""
Elo rating calculator.

Deterministic, auditable team-strength ratings. Every rating change is a
pure function of (old ratings, score, K-factor, HFA bonus) -- run the same
history through twice and you get identical numbers every time.

Per-league constants:
- STARTING_RATING   seed rating for a team with no history.
- K_FACTOR          rating sensitivity. Higher K = ratings move faster. NFL
                    is traditionally 20 (short season, high signal per game);
                    MLB is 4 (long season, noisy outcomes).
- HFA_RATING        Elo-point bonus added to the home team's rating when
                    computing the expected score for their game.
- DRAW_VALUE        score credit for a draw (0.5 by default).

These are starting points -- tune on backtested data in a later phase.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Tuple

from edge_equation.stats.results import GameResult


STARTING_RATING = Decimal('1500')

K_FACTOR = {
    "NFL": Decimal('20'),
    "NBA": Decimal('20'),
    "NHL": Decimal('6'),
    "MLB": Decimal('4'),
    "KBO": Decimal('4'),
    "NPB": Decimal('4'),
    "SOC": Decimal('10'),
    "NCAA_Basketball": Decimal('18'),
    "NCAA_Football": Decimal('18'),
}

HFA_RATING = {
    "NFL": Decimal('48'),    # ~1.8 points expressed in Elo
    "NBA": Decimal('60'),
    "NHL": Decimal('30'),
    "MLB": Decimal('20'),
    "KBO": Decimal('20'),
    "NPB": Decimal('20'),
    "SOC": Decimal('60'),
    "NCAA_Basketball": Decimal('75'),
    "NCAA_Football": Decimal('65'),
}


@dataclass(frozen=True)
class EloRatings:
    """Snapshot of team -> rating for one league, plus the underlying count."""
    league: str
    ratings: Dict[str, Decimal]
    games_seen: Dict[str, int]

    def rating_for(self, team: str) -> Decimal:
        return self.ratings.get(team, STARTING_RATING)

    def games_for(self, team: str) -> int:
        return self.games_seen.get(team, 0)

    def to_dict(self) -> dict:
        return {
            "league": self.league,
            "ratings": {t: str(r) for t, r in self.ratings.items()},
            "games_seen": dict(self.games_seen),
        }


class EloCalculator:
    """
    Stateless rating math + series replay:
    - expected_score(rating_a, rating_b) -> Decimal in [0, 1]
    - update(rating_home, rating_away, home_score, away_score, k, hfa)
        -> (new_home, new_away)
    - replay(league, results) -> EloRatings (walks every game in time order)
    - win_probability(league, home_team, away_team, ratings)
        -> Decimal home win prob, using league HFA
    """

    @staticmethod
    def expected_score(rating_a: Decimal, rating_b: Decimal) -> Decimal:
        # Elo expected score: 1 / (1 + 10^((R_b - R_a) / 400))
        import math
        diff = float(rating_b - rating_a)
        expected = Decimal(str(1.0 / (1.0 + math.pow(10.0, diff / 400.0))))
        return expected.quantize(Decimal('0.000001'))

    @staticmethod
    def update(
        rating_home: Decimal,
        rating_away: Decimal,
        home_score: int,
        away_score: int,
        k: Decimal,
        hfa: Decimal = Decimal('0'),
    ) -> Tuple[Decimal, Decimal]:
        """
        Return (new_home_rating, new_away_rating) after a single game.
        hfa is added to the home team's rating ONLY for the expected-score
        calculation; the ratings themselves are updated off the original
        values to avoid baking HFA into every team's long-run baseline.
        """
        expected_home = EloCalculator.expected_score(rating_home + hfa, rating_away)
        if home_score > away_score:
            score_home = Decimal('1')
        elif home_score < away_score:
            score_home = Decimal('0')
        else:
            score_home = Decimal('0.5')
        delta = k * (score_home - expected_home)
        new_home = (rating_home + delta).quantize(Decimal('0.000001'))
        new_away = (rating_away - delta).quantize(Decimal('0.000001'))
        return new_home, new_away

    @staticmethod
    def replay(league: str, results: List[GameResult]) -> EloRatings:
        """
        Walk results in start_time ASC order, updating Elo ratings. Returns
        the final EloRatings snapshot.
        """
        k = K_FACTOR.get(league, Decimal('10'))
        hfa = HFA_RATING.get(league, Decimal('30'))
        ratings: Dict[str, Decimal] = {}
        counts: Dict[str, int] = {}

        ordered = sorted(results, key=lambda g: g.start_time)
        for g in ordered:
            if g.league != league:
                continue
            rh = ratings.get(g.home_team, STARTING_RATING)
            ra = ratings.get(g.away_team, STARTING_RATING)
            new_rh, new_ra = EloCalculator.update(
                rh, ra, g.home_score, g.away_score, k=k, hfa=hfa,
            )
            ratings[g.home_team] = new_rh
            ratings[g.away_team] = new_ra
            counts[g.home_team] = counts.get(g.home_team, 0) + 1
            counts[g.away_team] = counts.get(g.away_team, 0) + 1

        return EloRatings(league=league, ratings=ratings, games_seen=counts)

    @staticmethod
    def win_probability(
        league: str,
        home_team: str,
        away_team: str,
        ratings: EloRatings,
    ) -> Decimal:
        hfa = HFA_RATING.get(league, Decimal('30'))
        rh = ratings.rating_for(home_team)
        ra = ratings.rating_for(away_team)
        return EloCalculator.expected_score(rh + hfa, ra)
