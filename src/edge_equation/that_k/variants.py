"""
That K Report -- A/B model variants for the Testing Ground.

The production projection uses the negative-binomial + Monte Carlo
path in model.py / simulator.py.  This module ships a second
alternative -- a Beta-Binomial / Bayesian-shrinkage K-rate model --
so both projections can run side-by-side per slate.  The debug
metrics JSON logs each variant's projected mean alongside the
actual result, giving the main Edge Equation engine real MAE /
calibration evidence for future model weighting.

Beta-Binomial rationale:
    Treat each plate appearance as an independent Bernoulli trial
    with unknown K probability p.  Prior belief: p ~ Beta(alpha, beta)
    with the prior tuned to the MLB league mean K/BF (~0.235).  After
    observing the pitcher's recent history (k strikeouts out of n PAs)
    the posterior is Beta(alpha + k, beta + n - k).  The posterior
    mean p_hat then multiplies the expected-BF count to produce the
    projected K mean for the start.

Shrinkage toward the league mean is the key calibration difference
vs the NB path.  A pitcher with only 3 recent starts gets pulled
toward the league baseline; a pitcher with 20 starts dominates the
posterior.  This tames the noisy-SP problem where NB + thin history
over-weights a single outlier start.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from edge_equation.that_k.model import (
    LEAGUE_K_PER_BF,
    PitcherProfile,
    OpponentLineup,
    GameContext,
    project_strikeouts,
)
from edge_equation.that_k.simulator import DEFAULT_N_SIMS


# Prior strength for the Beta(alpha, beta) base.  Equivalent sample
# size of 600 PAs ~= a full season of reference data, light enough to
# let 20+ recent starts move the posterior meaningfully.
_PRIOR_SAMPLE_SIZE = 600.0


@dataclass(frozen=True)
class VariantProjection:
    """One model's projection for a single pitcher start."""
    variant: str              # "nb_mc" | "beta_binomial"
    projected_mean: Decimal   # mean K count
    posterior_p: Optional[Decimal] = None   # beta-binomial p_hat
    posterior_n: Optional[int] = None       # effective sample size used

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "projected_mean": str(self.projected_mean),
            "posterior_p": str(self.posterior_p) if self.posterior_p is not None else None,
            "posterior_n": self.posterior_n,
        }


def _quantize(x: float, places: int = 2) -> Decimal:
    q = Decimal("1").scaleb(-places)
    return Decimal(str(x)).quantize(q)


def project_beta_binomial(
    pitcher: PitcherProfile,
    lineup: OpponentLineup,
    context: GameContext,
) -> VariantProjection:
    """Beta-Binomial posterior projection.  Pure arithmetic, no RNG --
    the MC path handles variance.  Returns a VariantProjection carrying
    the posterior p_hat and the projected mean K count.
    """
    # Prior Beta(alpha_0, beta_0) centered on the league K/BF.
    alpha_0 = _PRIOR_SAMPLE_SIZE * LEAGUE_K_PER_BF
    beta_0 = _PRIOR_SAMPLE_SIZE * (1.0 - LEAGUE_K_PER_BF)

    # Convert recent history into (k, n) pseudo-counts.  Each recent
    # start contributes ~ (BF) PAs; we don't have exact BF per start
    # stored, so we use the pitcher's expected_bf as the per-start
    # multiplier -- good enough for the posterior drag direction.
    recent = pitcher.recent_k_per_bf or []
    k_obs = 0.0
    n_obs = 0.0
    for k_per_bf, _age_days in recent:
        n_obs += pitcher.expected_bf
        k_obs += pitcher.expected_bf * float(k_per_bf)

    # Season-level K/BF baseline folds into the posterior too, but
    # with smaller weight than recent starts (half a prior's worth)
    # so a cold pitcher doesn't over-pin to a full-season number.
    season_weight = _PRIOR_SAMPLE_SIZE * 0.5
    season_k = season_weight * float(pitcher.k_per_bf)
    season_n = season_weight

    alpha_post = alpha_0 + k_obs + season_k
    beta_post = beta_0 + (n_obs - k_obs) + (season_n - season_k)
    denom = alpha_post + beta_post
    if denom <= 0:
        p_hat = LEAGUE_K_PER_BF
    else:
        p_hat = alpha_post / denom

    # Fold the SAME multiplicative matchup adjustments the NB path
    # applies so BOTH variants see identical matchup signal -- the
    # only thing that differs is the base K-rate model.
    shared = project_strikeouts(pitcher, lineup, context)
    base = p_hat * pitcher.expected_bf
    projected_mean = base * shared.total_adj

    return VariantProjection(
        variant="beta_binomial",
        projected_mean=_quantize(projected_mean, 2),
        posterior_p=_quantize(p_hat, 4),
        posterior_n=int(denom),
    )


def nb_projection_as_variant(projected_mean: float) -> VariantProjection:
    """Wrap the production NB+MC path's mean in the shared variant
    shape so the A/B logger can pair the two numbers directly."""
    return VariantProjection(
        variant="nb_mc",
        projected_mean=_quantize(projected_mean, 2),
    )


# ---------------------------------------------------------------- A/B log

@dataclass(frozen=True)
class ABEntry:
    """One slate row's per-variant projections + (optionally) the
    settled outcome for MAE comparison downstream."""
    pitcher: str
    team: str
    opponent: str
    nb_mean: Decimal
    bb_mean: Decimal
    line: Optional[float] = None
    actual: Optional[int] = None

    def nb_error(self) -> Optional[float]:
        if self.actual is None:
            return None
        return float(self.actual) - float(self.nb_mean)

    def bb_error(self) -> Optional[float]:
        if self.actual is None:
            return None
        return float(self.actual) - float(self.bb_mean)

    def to_dict(self) -> dict:
        return {
            "pitcher": self.pitcher,
            "team": self.team,
            "opponent": self.opponent,
            "nb_mean": str(self.nb_mean),
            "bb_mean": str(self.bb_mean),
            "line": self.line,
            "actual": self.actual,
            "nb_error": self.nb_error(),
            "bb_error": self.bb_error(),
        }


def ab_summary(entries: List[ABEntry]) -> dict:
    """Compare MAE for NB vs Beta-Binomial across settled entries.
    Missing `actual` values are skipped so the caller can log the
    per-row projections even before results settle."""
    nb_errs: List[float] = []
    bb_errs: List[float] = []
    for e in entries:
        nb = e.nb_error()
        bb = e.bb_error()
        if nb is not None:
            nb_errs.append(abs(nb))
        if bb is not None:
            bb_errs.append(abs(bb))
    return {
        "n_settled": len(nb_errs),
        "nb_mae": (sum(nb_errs) / len(nb_errs)) if nb_errs else None,
        "bb_mae": (sum(bb_errs) / len(bb_errs)) if bb_errs else None,
    }
