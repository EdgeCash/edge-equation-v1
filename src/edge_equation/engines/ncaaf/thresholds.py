"""Strict "Facts. Not Feelings." parlay rules — NCAAF universe.

Mirrors `engines.nfl.thresholds`. The audit-locked numerics import
from MLB so MLB / WNBA / NFL / NCAAF stay in lockstep on policy.
Only the allowed market sets diverge.
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


# NCAAF-allowed game-results markets — same surface as NFL since
# books post the same set on college football.
ALLOWED_NCAAF_GAME_RESULT_MARKETS: FrozenSet[str] = frozenset({
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


# NCAAF player-prop markets. The college-football prop inventory is
# narrower than the NFL's on some books (e.g. anytime-TD is the
# dominant offering), but `engines.football_core.markets.PROP_MARKET_LABELS`
# covers the full overlap; the strict gate accepts everything in
# that set so a future expansion drops in without changes here.
ALLOWED_NCAAF_PLAYER_PROP_MARKETS: FrozenSet[str] = frozenset({
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


@dataclass(frozen=True)
class NCAAFParlayRules:
    """Immutable snapshot of the strict-policy thresholds — NCAAF flavour."""

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
        default_factory=lambda: ALLOWED_NCAAF_GAME_RESULT_MARKETS,
    )
    allowed_player_prop_markets: FrozenSet[str] = field(
        default_factory=lambda: ALLOWED_NCAAF_PLAYER_PROP_MARKETS,
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


NCAAF_PARLAY_RULES: NCAAFParlayRules = NCAAFParlayRules()


__all__ = [
    "ALLOWED_NCAAF_GAME_RESULT_MARKETS",
    "ALLOWED_NCAAF_PLAYER_PROP_MARKETS",
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
    "NCAAFParlayRules",
    "NCAAF_PARLAY_RULES",
]
