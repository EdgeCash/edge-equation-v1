"""
Rolling per-team offensive, defensive, and pace summaries.

Given the last N games for a team, compute:
- scoring_rate   = avg points scored / league avg points scored   (1.0 = avg)
- allowed_rate   = avg points allowed / league avg points allowed (1.0 = avg,
                   higher = weaker defense / more scoring allowed)
- pace_rate      = avg total points per game / league avg total    (1.0 = avg)

These rates plug directly into the probability module's expected-total math:
  baseline_total * off_env * def_env * pace  where off_env and def_env come
from the matchup-combined values computed by TeamStats.matchup_factors.

Sample-size shrinkage: when a team has fewer than N_PRIOR games we blend
their observed rate with the league average using n / (n + N_PRIOR). Same
shape as the Phase 7a adaptive-Kelly shrinkage.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from edge_equation.stats.results import GameResult


N_PRIOR_GAMES = 15  # sample-size half-weight: teams converge to league avg below this


@dataclass(frozen=True)
class TeamRates:
    """Rolling per-team rates normalized to the league average (1.0)."""
    team: str
    league: str
    games: int
    scoring_rate: Decimal       # 1.0 = league-average offense
    allowed_rate: Decimal       # 1.0 = league-average points allowed
    pace_rate: Decimal          # 1.0 = league-average total points per game

    def to_dict(self) -> dict:
        return {
            "team": self.team,
            "league": self.league,
            "games": self.games,
            "scoring_rate": str(self.scoring_rate),
            "allowed_rate": str(self.allowed_rate),
            "pace_rate": str(self.pace_rate),
        }


@dataclass(frozen=True)
class MatchupFactors:
    """Combined matchup factors ready to feed the math layer."""
    off_env: Decimal
    def_env: Decimal
    pace: Decimal
    home: TeamRates
    away: TeamRates

    def to_dict(self) -> dict:
        return {
            "off_env": str(self.off_env),
            "def_env": str(self.def_env),
            "pace": str(self.pace),
            "home": self.home.to_dict(),
            "away": self.away.to_dict(),
        }


class TeamStats:
    """
    Rolling team-level summaries:
    - league_averages(results) -> {scoring, pace} averages across all teams
    - rates_for(team, league, results, league_avgs=None) -> TeamRates
    - matchup_factors(home, away, league, results) -> MatchupFactors

    The Decimal math is quantized to 0.000001 throughout to stay byte-for-byte
    deterministic across platforms.
    """

    @staticmethod
    def league_averages(results: List[GameResult]) -> Dict[str, Decimal]:
        """
        Return {"scoring": avg-points-per-team-per-game, "pace": avg-total-per-game}
        over the supplied results. A result contributes 2 team-game observations
        (home and away) to the scoring average, 1 total observation to pace.
        """
        if not results:
            return {"scoring": Decimal('1').quantize(Decimal('0.000001')),
                    "pace": Decimal('1').quantize(Decimal('0.000001'))}
        team_games = Decimal(len(results) * 2)
        total_points = Decimal(sum(r.home_score + r.away_score for r in results))
        avg_scoring = (total_points / team_games).quantize(Decimal('0.000001'))
        avg_pace = (total_points / Decimal(len(results))).quantize(Decimal('0.000001'))
        return {"scoring": avg_scoring, "pace": avg_pace}

    @staticmethod
    def _shrink(sample_rate: Decimal, sample_n: int, prior: Decimal = Decimal('1')) -> Decimal:
        n = Decimal(max(0, int(sample_n)))
        weight_sample = n / (n + Decimal(N_PRIOR_GAMES))
        weight_prior = Decimal('1') - weight_sample
        return (weight_sample * sample_rate + weight_prior * prior).quantize(Decimal('0.000001'))

    @staticmethod
    def rates_for(
        team: str,
        league: str,
        results: List[GameResult],
        league_avgs: Optional[Dict[str, Decimal]] = None,
    ) -> TeamRates:
        team_games = [r for r in results if r.league == league and team in (r.home_team, r.away_team)]
        if league_avgs is None:
            league_avgs = TeamStats.league_averages(
                [r for r in results if r.league == league]
            )
        league_scoring = league_avgs["scoring"]
        league_pace = league_avgs["pace"]

        if not team_games or league_scoring == Decimal('0') or league_pace == Decimal('0'):
            # New team or empty league history -> everyone is league average.
            one = Decimal('1').quantize(Decimal('0.000001'))
            return TeamRates(
                team=team, league=league, games=len(team_games),
                scoring_rate=one, allowed_rate=one, pace_rate=one,
            )

        scored = Decimal(sum(
            r.home_score if r.home_team == team else r.away_score for r in team_games
        ))
        allowed = Decimal(sum(
            r.away_score if r.home_team == team else r.home_score for r in team_games
        ))
        total = Decimal(sum(r.home_score + r.away_score for r in team_games))
        n = len(team_games)
        sample_scoring = (scored / Decimal(n) / league_scoring)
        sample_allowed = (allowed / Decimal(n) / league_scoring)
        sample_pace = (total / Decimal(n) / league_pace)

        return TeamRates(
            team=team,
            league=league,
            games=n,
            scoring_rate=TeamStats._shrink(sample_scoring.quantize(Decimal('0.000001')), n),
            allowed_rate=TeamStats._shrink(sample_allowed.quantize(Decimal('0.000001')), n),
            pace_rate=TeamStats._shrink(sample_pace.quantize(Decimal('0.000001')), n),
        )

    @staticmethod
    def matchup_factors(
        home: str,
        away: str,
        league: str,
        results: List[GameResult],
    ) -> MatchupFactors:
        """
        Compose per-team rates into the matchup-level off_env, def_env, pace
        that the ProbabilityCalculator totals math expects.

        - off_env: average of both teams' offenses
        - def_env: average of both teams' allowed rates (higher = weaker defenses,
                   so baseline * def_env scales totals up)
        - pace:    average of the two teams' pace rates
        """
        league_avgs = TeamStats.league_averages(
            [r for r in results if r.league == league]
        )
        h = TeamStats.rates_for(home, league, results, league_avgs)
        a = TeamStats.rates_for(away, league, results, league_avgs)
        off_env = ((h.scoring_rate + a.scoring_rate) / Decimal('2')).quantize(Decimal('0.000001'))
        def_env = ((h.allowed_rate + a.allowed_rate) / Decimal('2')).quantize(Decimal('0.000001'))
        pace = ((h.pace_rate + a.pace_rate) / Decimal('2')).quantize(Decimal('0.000001'))
        return MatchupFactors(
            off_env=off_env, def_env=def_env, pace=pace, home=h, away=a,
        )
