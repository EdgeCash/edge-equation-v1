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
        home_pitching=None,
        away_pitching=None,
    ) -> ComposedFeatures:
        """
        Phase 19: runs the full TeamStrengthBuilder blend (Pythagorean +
        decay-weighted form + Elo + optional pitching) instead of mapping
        Elo straight to BT strength. Pure function of (home, away, league,
        results, elo, pitching). home_adv comes from SPORT_CONFIG.
        """
        from edge_equation.config.sport_config import SportConfig
        from edge_equation.stats.team_strength import TeamStrengthBuilder

        scoped = [r for r in results if r.league == league]
        if elo is None:
            elo = EloCalculator.replay(league, scoped)
        matchup = TeamStats.matchup_factors(home, away, league, scoped)

        home_strength = TeamStrengthBuilder.build(
            team=home, league=league, results=scoped, elo=elo,
            pitching=home_pitching,
        )
        away_strength = TeamStrengthBuilder.build(
            team=away, league=league, results=scoped, elo=elo,
            pitching=away_pitching,
        )

        ml_inputs = {
            "strength_home": float(home_strength.strength),
            "strength_away": float(away_strength.strength),
            "home_adv": float(SportConfig.home_adv(league)),
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

        Phase 31 changes:
          - The composer runs even when `results` is empty. Previously the
            scheduled runner skipped enrich_markets entirely when no
            historical results were stored, leaving every ML market with
            no inputs and BettingEngine falling back to strength=1.0/1.0.
            With Team_StrengthBuilder's cold-start seed, strengths are
            now per-team-deterministic even on day zero of a league.
          - Each enriched market also gets a meta['read_context'] dict
            stashed alongside meta['inputs']. This is the structured
            evidence the betting_engine's _baseline_read consumes to
            produce a substantive "Read:" line: games-used, recent form
            string, run differential, Elo edge, etc. NEVER includes tout
            language -- it's facts the renderer can quote verbatim.

        Returns a new list (inputs list is not mutated).
        """
        games_by_id = {g["game_id"]: g for g in raw_games if g.get("league") == league}
        scoped_results = [r for r in results if r.league == league]
        elo = EloCalculator.replay(league, scoped_results)
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
            home = game["home_team"]
            away = game["away_team"]
            composed = FeatureComposer.compose(
                home=home, away=away, league=league,
                results=results, elo=elo,
            )
            market_type = market.get("market_type")
            if market_type in ("ML", "Run_Line", "Puck_Line", "Spread"):
                meta["inputs"] = dict(composed.ml_inputs)
            elif market_type in ("Total", "Game_Total"):
                meta["inputs"] = dict(composed.totals_inputs)
            else:
                enriched.append(market)
                continue
            # Phase 31: stash structured evidence the engine can quote
            # in its Read line. Every key here is FACTUAL data pulled
            # from game results / Elo replay -- the renderer never
            # invents narrative on top.
            meta["read_context"] = FeatureComposer._read_context(
                home=home, away=away, league=league,
                scoped_results=scoped_results, elo=elo,
            )
            meta.setdefault("universal_features", {})
            new_market = dict(market)
            new_market["meta"] = meta
            enriched.append(new_market)
        return enriched

    # --------------------------------------------------- read context

    @staticmethod
    def _read_context(
        home: str,
        away: str,
        league: str,
        scoped_results: List[GameResult],
        elo: EloRatings,
    ) -> Dict[str, object]:
        """Phase 31: produce the FACTUAL evidence block that powers the
        engine's Read line. No tout language, no narrative -- just numbers
        and short strings the renderer composes into prose downstream.

        Keys (all optional; renderer skips missing ones):
          - games_used_home, games_used_away: settled-game counts
          - recent_form_home, recent_form_away: e.g. "8-3 L11"
          - run_diff_home, run_diff_away: total runs scored - allowed in window
          - elo_home, elo_away: Elo ratings (ints)
          - elo_diff: home - away Elo delta (int)
          - sample_warning: True iff either side has < 8 settled games
            (drives the "limited history -- league prior dominates" caveat)
        """
        from edge_equation.config.sport_config import SportConfig
        window = int(SportConfig.form_window_games(league))

        def _team_window(team: str) -> List[GameResult]:
            tg = [g for g in scoped_results if team in (g.home_team, g.away_team)]
            return sorted(tg, key=lambda g: g.start_time, reverse=True)[:window]

        def _wl_string(team: str, games: List[GameResult]) -> Optional[str]:
            if not games:
                return None
            wins = losses = 0
            for g in games:
                if g.is_draw():
                    continue
                team_won = (
                    (g.home_team == team and g.home_won())
                    or (g.away_team == team and not g.home_won())
                )
                if team_won:
                    wins += 1
                else:
                    losses += 1
            return f"{wins}-{losses} L{len(games)}"

        def _run_diff(team: str, games: List[GameResult]) -> Optional[int]:
            if not games:
                return None
            rs = sum(g.home_score if g.home_team == team else g.away_score for g in games)
            ra = sum(g.away_score if g.home_team == team else g.home_score for g in games)
            return int(rs - ra)

        home_games = _team_window(home)
        away_games = _team_window(away)
        ctx: Dict[str, object] = {
            "games_used_home": len(home_games),
            "games_used_away": len(away_games),
        }
        wf_h = _wl_string(home, home_games)
        wf_a = _wl_string(away, away_games)
        if wf_h:
            ctx["recent_form_home"] = wf_h
        if wf_a:
            ctx["recent_form_away"] = wf_a
        rd_h = _run_diff(home, home_games)
        rd_a = _run_diff(away, away_games)
        if rd_h is not None:
            ctx["run_diff_home"] = rd_h
        if rd_a is not None:
            ctx["run_diff_away"] = rd_a
        if elo is not None:
            r_h = elo.ratings.get(home)
            r_a = elo.ratings.get(away)
            if r_h is not None:
                ctx["elo_home"] = int(r_h)
            if r_a is not None:
                ctx["elo_away"] = int(r_a)
            if r_h is not None and r_a is not None:
                ctx["elo_diff"] = int(r_h) - int(r_a)
        # Sample warning -- the engine quotes this so subscribers know
        # when a projection is leaning on the league prior.
        if len(home_games) < 8 or len(away_games) < 8:
            ctx["sample_warning"] = True
        return ctx
