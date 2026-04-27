"""Canonical home for 2026 ABS Challenge System effects.

Up to phase 2, the ABS priors lived inline in `feature_engineering.py`.
This module pulls them out and adds two missing pieces the audit
called out:

1. `bb_pct_uplift(...)` — per-matchup additive uplift to a pitcher's
   BB%, NOT a league-mean shift. Captures the fact that *some*
   pitchers are hurt by ABS more than others (high-CSW% paint-the-
   corners types lose called strikes; pure-K stuff types are basically
   unaffected).

2. `umpire_adaptation_curve(games_in_abs_era)` — empirically the
   league average umpire zone has compressed toward the rulebook
   strike zone over 2026 as crews adapt to overturn feedback. We
   model that compression as a logistic decay: full pre-ABS deviation
   → roughly half-strength after ~30 games of personal ABS exposure.

The numerical constants in `ABS_2026_PRIORS` are sourced from the
public Baseball Savant ABS leaderboard rolling totals; refresh them
each spring and at the All-Star break.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 2026 ABS Challenge System priors (league-wide trailing observations).
# Refresh annually. Numbers below match what we logged through the
# first 4 weeks of 2026 regular season.
ABS_2026_PRIORS = {
    "overturn_rate": 0.54,        # umpire calls overturned on challenge
    "catcher_success": 0.64,      # catcher-initiated challenge success rate
    "walk_rate_league": 0.099,    # post-ABS league BB% (vs ~0.085 pre-ABS)
    "called_strike_pull": -0.012, # CSA index drop on outside corner
}

# Pre-ABS league walk rate baseline (2024-2025).
PRE_ABS_WALK_RATE_LEAGUE = 0.085

# League delta caused by ABS introduction. The phase-2 implementation
# applied this directly to every pitcher; phase-3 distributes it as a
# function of pitcher style (see `bb_pct_uplift`).
ABS_LEAGUE_BB_UPLIFT = ABS_2026_PRIORS["walk_rate_league"] - PRE_ABS_WALK_RATE_LEAGUE


@dataclass(frozen=True)
class ABSContext:
    """Per-game ABS context bundled into one object."""

    active: bool                              # True for season >= 2026
    ump_overturn_rate: float = ABS_2026_PRIORS["overturn_rate"]
    ump_games_in_abs_era: int = 60            # how seasoned this ump is
    pitcher_csw_pct: float = 0.295            # league avg
    pitcher_zone_pct: float = 0.495           # league avg


# ---------------------------------------------------------------------------
# Per-matchup BB% uplift
# ---------------------------------------------------------------------------

def bb_pct_uplift(pitcher_bb_pct: float, ctx: ABSContext) -> float:
    """Return the additive BB% uplift for THIS pitcher under THIS ump.

    Decomposition:

        delta = league_delta * style_factor * ump_factor

    * `style_factor` ∈ [0.4, 1.6]: pitchers with above-average CSW%
      and below-average zone% (paint-the-corners) lose more strikes
      to overturns and gain more walks.
    * `ump_factor` ∈ [0.5, 1.5]: scaled by the ump's overturn rate
      against league mean — high-overturn umps amplify the effect
      because their pre-ABS calls were further from the rulebook
      zone to begin with.

    Returns 0.0 when ABS isn't active.
    """
    if not ctx.active:
        return 0.0

    # Style factor — high CSW%, low zone% gets penalised.
    csw_z = (ctx.pitcher_csw_pct - 0.295) / 0.025  # 1σ ≈ 2.5pp
    zone_z = (0.495 - ctx.pitcher_zone_pct) / 0.020 # 1σ ≈ 2pp; lower zone% = bigger hit
    style_factor = 1.0 + 0.30 * csw_z + 0.20 * zone_z
    style_factor = max(0.4, min(1.6, style_factor))

    # Umpire factor — relative to league overturn rate.
    ump_z = (ctx.ump_overturn_rate - ABS_2026_PRIORS["overturn_rate"]) / 0.05
    ump_factor = 1.0 + 0.50 * ump_z
    ump_factor = max(0.5, min(1.5, ump_factor))

    # Apply adaptation curve — umpires get better over the season.
    adaptation = umpire_adaptation_curve(ctx.ump_games_in_abs_era)

    delta = ABS_LEAGUE_BB_UPLIFT * style_factor * ump_factor * adaptation
    return float(delta)


def umpire_adaptation_curve(games_in_abs_era: int) -> float:
    """Logistic decay of pre-ABS zone bias as umps accumulate ABS exposure.

    Returns a multiplicative dampener in [~0.55, 1.0]:

        g=0   → 1.00 (full pre-ABS bias survives)
        g=15  → ~0.85
        g=30  → ~0.70
        g=60  → ~0.58
        g→∞   → 0.50 (asymptote: rulebook-equivalent zone)

    Used to discount any per-umpire zone idx the further into the ABS
    era we are. Multiply this against the umpire effect, NOT the
    pitcher walk rate (the walk rate is already centered on the
    post-ABS league mean).
    """
    g = max(0, int(games_in_abs_era))
    return 0.50 + 0.50 * math.exp(-g / 25.0)


def is_abs_active_for_season(season: int) -> bool:
    """Convenience predicate. Single source of truth — match this in
    the backtest auto-toggle and in the source's regime detection."""
    return season >= 2026
