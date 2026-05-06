"""Strict "Facts. Not Feelings." parlay rules — WNBA universe.

Mirrors `engines.mlb.thresholds` exactly. Same strict policy
(3–6 legs, ≥4pp edge OR ELITE, EV>0 after vig, no forcing) but
keyed on the WNBA market vocabulary instead of MLB's. Every
constant is read from a single place so edits stay tight.

The strict-rule numerics (`MIN_LEG_EDGE_FRAC`, `MAX_LEGS`, etc.)
import from the MLB module so the two sports never drift apart
on the audit-locked policy. WNBA-specific surface area is just
the allowed market sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet

from edge_equation.engines.tiering import Tier

# Re-use the audit-locked numerics from MLB so any future tightening
# applies to both sports automatically. The MLB module is the policy
# source-of-truth.
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
# Allowed WNBA market sets — strict-policy gate rejects anything else.
# ---------------------------------------------------------------------------


# Game-results markets the WNBA engine projects on. Names match the
# canonical strings the WNBA `Output.market` enum produces (lowercase
# `fullgame_*`) plus a `team_total` alias for the per-team total
# market that the daily slate exposes when the book posts it.
ALLOWED_WNBA_GAME_RESULT_MARKETS: FrozenSet[str] = frozenset({
    "fullgame_ml",
    "fullgame_spread",
    "fullgame_total",
    "team_total",
})


# Player-prop markets covered by the WNBA player-props parlay engine.
# Lowercase names match what `engines.wnba.schema.Market` emits today
# (`points`, `rebounds`, `assists`, `pra`, `3pm`) plus the audit's
# expanded set (`steals`, `blocks`, `turnovers`, `pr`, `pa`, `ra`).
# Markets the engine doesn't yet project on are still enumerated here
# so a future expansion drops in without touching the rule module.
ALLOWED_WNBA_PLAYER_PROP_MARKETS: FrozenSet[str] = frozenset({
    "points",
    "rebounds",
    "assists",
    "pra",          # points + rebounds + assists
    "pr",           # points + rebounds
    "pa",           # points + assists
    "ra",           # rebounds + assists
    "3pm",
    "steals",
    "blocks",
    "stocks",       # steals + blocks (PrizePicks-style)
    "turnovers",
    "minutes",
})


# ---------------------------------------------------------------------------
# Convenience dataclass — frozen view callers pass around.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WNBAParlayRules:
    """Immutable snapshot of the strict-policy thresholds, WNBA flavour.

    All numerics are sourced from `engines.mlb.thresholds` so the two
    sports stay in lock-step. Only the allowed-market sets diverge.
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
        default_factory=lambda: ALLOWED_WNBA_GAME_RESULT_MARKETS,
    )
    allowed_player_prop_markets: FrozenSet[str] = field(
        default_factory=lambda: ALLOWED_WNBA_PLAYER_PROP_MARKETS,
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
        """Strict per-leg gate — same shape as MLB's. Returns True iff
        the leg passes every audit check for the chosen universe."""
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


WNBA_PARLAY_RULES: WNBAParlayRules = WNBAParlayRules()


__all__ = [
    "ALLOWED_WNBA_GAME_RESULT_MARKETS",
    "ALLOWED_WNBA_PLAYER_PROP_MARKETS",
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
    "WNBAParlayRules",
    "WNBA_PARLAY_RULES",
]
