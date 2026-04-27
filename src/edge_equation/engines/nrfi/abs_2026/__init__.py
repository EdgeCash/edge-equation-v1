"""Canonical 2026 ABS Challenge System effects.

Use this module as the single source of truth for ABS priors, BB%
uplift modeling, and umpire adaptation. The phase-1 inline definitions
in `nrfi.features.feature_engineering` are kept as deprecated aliases
for backward compatibility but new code should import from here.
"""

from .effects import (
    ABS_2026_PRIORS,
    ABS_LEAGUE_BB_UPLIFT,
    ABSContext,
    PRE_ABS_WALK_RATE_LEAGUE,
    bb_pct_uplift,
    is_abs_active_for_season,
    umpire_adaptation_curve,
)

__all__ = [
    "ABS_2026_PRIORS",
    "ABS_LEAGUE_BB_UPLIFT",
    "ABSContext",
    "PRE_ABS_WALK_RATE_LEAGUE",
    "bb_pct_uplift",
    "is_abs_active_for_season",
    "umpire_adaptation_curve",
]
