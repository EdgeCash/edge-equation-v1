"""Parlay builder config — defaults + env-var overrides.

The audit-locked policy for parlays:

* Min tier per leg: STRONG (LEAN/MODERATE bleed too easily on parlays).
* Max legs: 3 default. 4 legs is allowed but only via env override.
* Stake: 0.5u flat per parlay (variance control vs single-leg 1u).
* Joint-prob floor: model-implied (correlation-adjusted) probability
  must be ≥ 68% — sub-coinflip parlays are not "Special Drops."
* EV floor: projected EV ≥ +0.25u at the default 0.5u stake.

These knobs are environment-tuneable so the operator can dial the
filter strength after seeing the first week of candidates without a
code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from edge_equation.engines.tiering import Tier


# Env-var names — kept stable across versions so daily reports /
# workflows can override the defaults without modifying code.
ENV_MIN_TIER         = "PARLAY_MIN_TIER"
ENV_MAX_LEGS         = "PARLAY_MAX_LEGS"
ENV_DEFAULT_STAKE    = "PARLAY_DEFAULT_STAKE"
ENV_MIN_JOINT_PROB   = "PARLAY_MIN_JOINT_PROB"
ENV_MIN_EV_UNITS     = "PARLAY_MIN_EV_UNITS"
ENV_MC_TRIALS        = "PARLAY_MC_TRIALS"
ENV_MC_SEED          = "PARLAY_MC_SEED"


@dataclass(frozen=True)
class ParlayConfig:
    """All builder/qualification thresholds in one place."""
    min_tier: Tier = Tier.STRONG
    max_legs: int = 3
    default_stake_units: float = 0.5
    min_joint_prob: float = 0.68
    min_ev_units: float = 0.25
    mc_trials: int = 10_000
    mc_seed: int = 42

    # Hard cap on per-leg correlation magnitude. Used to keep the
    # Gaussian copula's correlation matrix positive semi-definite when
    # the lookup table contains values close to ±1 (e.g., "two legs in
    # the same game's first inning" should be excluded outright, not
    # passed through with ρ = 0.99). Same-market same-game pairs are
    # filtered upstream; this is a numerical-safety guardrail.
    max_abs_correlation: float = 0.85


def load_from_env() -> ParlayConfig:
    """Build a ParlayConfig with env-var overrides applied."""
    base = ParlayConfig()
    return ParlayConfig(
        min_tier=_tier_from_env(ENV_MIN_TIER, base.min_tier),
        max_legs=_int_from_env(ENV_MAX_LEGS, base.max_legs),
        default_stake_units=_float_from_env(
            ENV_DEFAULT_STAKE, base.default_stake_units),
        min_joint_prob=_float_from_env(
            ENV_MIN_JOINT_PROB, base.min_joint_prob),
        min_ev_units=_float_from_env(
            ENV_MIN_EV_UNITS, base.min_ev_units),
        mc_trials=_int_from_env(ENV_MC_TRIALS, base.mc_trials),
        mc_seed=_int_from_env(ENV_MC_SEED, base.mc_seed),
        max_abs_correlation=base.max_abs_correlation,
    )


def _tier_from_env(name: str, default: Tier) -> Tier:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return Tier(raw.strip().upper())
    except ValueError:
        return default


def _int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default
