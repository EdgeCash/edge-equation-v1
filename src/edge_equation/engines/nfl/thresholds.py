"""Strict "Facts. Not Feelings." parlay rules — NFL universe.

Mirrors `engines.wnba.thresholds` and `engines.mlb.thresholds`. The
audit-locked numerics import directly from MLB so the four sports
(MLB / WNBA / NFL / NCAAF) never drift apart on the policy. Only
the allowed market sets diverge per sport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet

from edge_equation.engines.tiering import Tier
from edge_equation.engines.mlb.thresholds import (
    MIN_LEGS,
    MAX_LEGS,
    MIN_LEG_EDGE_FRAC,
    ELITE_BYPASS_TIER,
    MIN_LEG_CONFIDENCE,
    MIN_JOINT_PROB,
    MIN_EV_UNITS,
    DEFAULT_STAKE_UNITS,
    MIN_LEG_CLV_PP,
    MAX_ABS_CORRELATION,
    MC_TRIALS,
    MC_SEED,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
    NO_QUALIFIED_PARLAY_MESSAGE,
)


# ---------------------------------------------------------------------------
# Allowed market sets — NFL flavour.
# ---------------------------------------------------------------------------


# Game-results markets the NFL engine projects on. Names match the
# canonical strings emitted by `engines.football_core.markets` plus
# the planned 1H / 1Q markets that book consistently posts on US
# sportsbooks. Keys are kept in the canonical Title_Case form the
# engine modules already use.
ALLOWED_NFL_GAME_RESULT_MARKETS: FrozenSet[str] = frozenset({
    "ML",
    "Spread",
    "Total",
    "Team_Total",
    "Alternate_Spread",
    "Alternate_Total",
    "First_Half_Spread",
    "First_Half_Total",
    "First_Half_ML",
    "First_Quarter_Spread",
    "First_Quarter_Total",
    "First_Quarter_ML",
})


# Player-prop markets — passing / rushing / receiving + anytime TD.
# Names match `football_core.markets.PROP_MARKET_LABELS` keys so the
# engine modules read the same strings the parlay gate reads.
ALLOWED_NFL_PLAYER_PROP_MARKETS: FrozenSet[str] = frozenset({
    "Pass_Yds",
    "Pass_TDs",
    "Pass_Att",
    "Pass_Comp",
    "Pass_Ints",
    "Rush_Yds",
    "Rush_Att",
    "Rush_TDs",
    "Rec_Yds",
    "Rec_Recs",
    "Rec_TDs",
    "Anytime_TD",
    "Longest_Rec",
    "Longest_Rush",
})


# ---------------------------------------------------------------------------
# Convenience dataclass.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NFLParlayRules:
    """Immutable snapshot of the strict-policy thresholds, NFL flavour.

    All numerics are sourced from `engines.mlb.thresholds`. Only the
    allowed-market sets diverge.
    """

    min_legs: int = MIN_LEGS
    max_legs: int = MAX_LEGS
    min_leg_edge_frac: float = MIN_LEG_EDGE_FRAC
    elite_bypass_tier: Tier = ELITE_BYPASS_TIER
    min_leg_confidence: float = MIN_LEG_CONFIDENCE
    min_joint_prob: float = MIN_JOINT_PROB
    min_ev_units: float = MIN_EV_UNITS
    default_stake_units: float = DEFAULT_STAKE_UNITS
    min_leg_clv_pp: float = MIN_LEG_CLV_PP
    max_abs_correlation: float = MAX_ABS_CORRELATION
    mc_trials: int = MC_TRIALS
    mc_seed: int = MC_SEED
    allowed_game_result_markets: FrozenSet[str] = field(
        default_factory=lambda: ALLOWED_NFL_GAME_RESULT_MARKETS,
    )
    allowed_player_prop_markets: FrozenSet[str] = field(
        default_factory=lambda: ALLOWED_NFL_PLAYER_PROP_MARKETS,
    )

    def leg_qualifies(
        self, *,
        market_type: str,
        edge_frac: float,
        tier: Tier,
        confidence: float,
        clv_pp: float = 0.0,
        market_universe: str = "game_results",
    ) -> bool:
        """Strict per-leg gate."""
        if market_universe == "game_results":
            allowed = self.allowed_game_result_markets
        elif market_universe == "player_props":
            allowed = self.allowed_player_prop_markets
        else:
            return False
        if market_type not in allowed:
            return False
        if confidence <= self.min_leg_confidence:
            return False
        if clv_pp < self.min_leg_clv_pp:
            return False
        if tier == self.elite_bypass_tier:
            return True
        return float(edge_frac) >= float(self.min_leg_edge_frac)


NFL_PARLAY_RULES: NFLParlayRules = NFLParlayRules()


__all__ = [
    "ALLOWED_NFL_GAME_RESULT_MARKETS",
    "ALLOWED_NFL_PLAYER_PROP_MARKETS",
    "MIN_LEGS",
    "MAX_LEGS",
    "MIN_LEG_EDGE_FRAC",
    "ELITE_BYPASS_TIER",
    "MIN_LEG_CONFIDENCE",
    "MIN_JOINT_PROB",
    "MIN_EV_UNITS",
    "DEFAULT_STAKE_UNITS",
    "MIN_LEG_CLV_PP",
    "MAX_ABS_CORRELATION",
    "MC_TRIALS",
    "MC_SEED",
    "PARLAY_CARD_NOTE",
    "PARLAY_TRANSPARENCY_NOTE",
    "NO_QUALIFIED_PARLAY_MESSAGE",
    "NFLParlayRules",
    "NFL_PARLAY_RULES",
]
