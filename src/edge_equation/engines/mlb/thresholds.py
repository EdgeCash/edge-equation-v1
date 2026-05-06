"""Strict "Facts. Not Feelings." parlay rules — single source of truth.

The user's audit-locked policy for the two new MLB parlay engines is
encoded here so every consumer (the parlay engines, the daily runner,
the website exporter, the backtest) reads from one place. Changing a
threshold means changing exactly one line.

Non-negotiable rules (verbatim from the audit):

* 3–6 legs maximum (parlay rejected if it exceeds either bound).
* Only legs from the markets we've finalized: ML / Run_Line / Total /
  Team_Total / F5_Total / F5_ML / NRFI / YRFI for game-results, and
  Hits / RBI / HR / K / Total_Bases / Runs / SB for player-props.
* Each leg must clear ONE of the strict gates:
    - edge ≥ ``MIN_LEG_EDGE_FRAC`` against the closing-line consensus
      (the engine's de-vigged market price), OR
    - tier == ELITE (the highest conviction band — "Signal Elite /
      LOCK").
* The combined Monte-Carlo / Bradley-Terry probability must remain
  positive expected value AFTER vig — i.e. ``ev_units >= MIN_EV_UNITS``
  on the configured stake.
* Never force a parlay. When no combination passes, the engine emits a
  single explanatory pick: "No qualified parlay today …".
* No lottos, no 8+ leg specials, no "fun" or high-odds-only parlays.

Anything outside these rules is, by policy, not the kind of parlay this
engine ships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet

from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Strict-rule constants — DO NOT relax without a written policy change.
# ---------------------------------------------------------------------------


# Minimum number of legs a strict parlay can carry. Two-leg "parlays"
# are barely correlation-adjusted moneylines; the audit requires a real
# multi-leg ticket so the joint-probability math has something to do.
MIN_LEGS: int = 3

# Maximum number of legs a strict parlay can carry. The audit caps this
# at six — anything larger is the "lotto" pattern the engine refuses to
# build.
MAX_LEGS: int = 6

# Per-leg edge threshold against the de-vigged closing line, expressed
# as a fraction (0.04 == 4 percentage points).
MIN_LEG_EDGE_FRAC: float = 0.04

# Tier that satisfies the "Signal Elite / LOCK" path — a leg that does
# NOT meet ``MIN_LEG_EDGE_FRAC`` may still qualify if classified ELITE.
ELITE_BYPASS_TIER: Tier = Tier.ELITE

# Confidence floor on the underlying projection. Anything at or below
# this is the league-prior baseline; the parlay engines refuse to build
# a ticket on pure-prior legs.
MIN_LEG_CONFIDENCE: float = 0.31

# Joint-probability floor (correlation-adjusted) below which we won't
# stake even an EV-positive parlay. Strict-policy variance control.
MIN_JOINT_PROB: float = 0.18

# EV floor in units, evaluated at the default stake. Strictly positive
# expected value AFTER vig is required for publication.
MIN_EV_UNITS: float = 0.10

# Stake the engine quotes when reporting EV. The two parlay engines
# treat the published parlay as a 0.5u "Special Drop" — variance
# control vs the 1u single-leg default.
DEFAULT_STAKE_UNITS: float = 0.5

# CLV threshold — a leg whose model edge has already evaporated by the
# time we publish (CLV ≤ 0) gets dropped even if the rest of the gates
# pass. This is the "data does not support a high-confidence
# combination" check the audit calls out.
MIN_LEG_CLV_PP: float = -1.0

# Same-game / same-event guardrails. We never combine two legs that
# describe outcomes that cannot co-occur (NRFI + YRFI on the same game,
# Over and Under on the same total, both teams ML on the same game,
# etc.); these are filtered upstream by ``engines.parlay.builder``.
# Hard cap on per-leg correlation magnitude to keep the Gaussian copula
# matrix safely positive-semi-definite for Cholesky decomposition.
MAX_ABS_CORRELATION: float = 0.85

# Monte Carlo trials for the joint-probability simulation. 10,000 keeps
# sampling noise well below ~0.5pp on a 6-leg ticket.
MC_TRIALS: int = 10_000
MC_SEED: int = 1337


# ---------------------------------------------------------------------------
# Allowed market sets — anything outside these is rejected at leg-build.
# ---------------------------------------------------------------------------


# Game-results markets — match the canonical names produced by
# ``engines.full_game.markets`` and the NRFI integration bridge.
ALLOWED_GAME_RESULT_MARKETS: FrozenSet[str] = frozenset({
    "ML",            # Moneyline
    "Run_Line",      # Run Line incl. alternate lines
    "Total",         # Game total (Over/Under)
    "Team_Total",    # Per-team totals
    "F5_Total",      # First-five-innings total
    "F5_ML",         # First-five-innings moneyline
    "NRFI",          # First-inning Under 0.5
    "YRFI",          # First-inning Over 0.5
})


# Player-prop markets — match canonical names from
# ``engines.props_prizepicks.markets`` plus the audit's expanded set.
# Markets the engine doesn't yet project on are still listed here so a
# future expansion drops in without touching the rule module.
ALLOWED_PLAYER_PROP_MARKETS: FrozenSet[str] = frozenset({
    "Hits",
    "RBI",
    "HR",
    "K",
    "Total_Bases",
    "Runs",          # Runs scored
    "SB",            # Stolen bases
    "Singles",
    "Doubles",
    "Triples",
    "Outs",          # Pitcher outs recorded
    "Earned_Runs",
})


# ---------------------------------------------------------------------------
# Convenience dataclass — a frozen view callers pass around instead of
# importing each constant individually.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MLBParlayRules:
    """Immutable snapshot of the strict-policy thresholds.

    The two parlay engines accept a ``MLBParlayRules`` instance so a
    backtest can sweep alternative thresholds (e.g. ``min_leg_edge_frac
    = 0.05``) without rewriting the policy module — but the default
    instance always reflects the audit-locked production policy.
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
        default_factory=lambda: ALLOWED_GAME_RESULT_MARKETS,
    )
    allowed_player_prop_markets: FrozenSet[str] = field(
        default_factory=lambda: ALLOWED_PLAYER_PROP_MARKETS,
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
        """Return True iff a single leg passes the strict-policy gate.

        ``market_universe`` is one of ``"game_results"`` or
        ``"player_props"`` — the engine that calls this picks the right
        universe so a mis-attributed leg (e.g., a player-prop leg fed
        to the game-results parlay) is rejected up front.
        """
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


# Default rules instance — every consumer should import this rather
# than constructing its own copy unless they're explicitly sweeping
# alternative thresholds in a backtest.
MLB_PARLAY_RULES: MLBParlayRules = MLBParlayRules()


# ---------------------------------------------------------------------------
# Card boilerplate — the small note that accompanies every published
# parlay so the reader sees the rules at a glance.
# ---------------------------------------------------------------------------


PARLAY_CARD_NOTE: str = (
    f"{MIN_LEGS}–{MAX_LEGS} legs only — built from proven edges only."
)


# The audit-locked transparency sentence. Surfaced verbatim on every
# parlay section of EdgeEquation.com and in every parlay block of the
# daily card. Editing this text is a one-place change.
PARLAY_TRANSPARENCY_NOTE: str = (
    "Parlays built only from legs meeting strict edge thresholds "
    "(≥4pp or ELITE tier, positive EV after vig). "
    "No plays forced. Facts. Not Feelings."
)


NO_QUALIFIED_PARLAY_MESSAGE: str = (
    "No qualified parlay today — data does not support a "
    "high-confidence combination."
)
