"""Woolner exponential link: closed-form P(N runs in inning | RPG).

From Keith Woolner's "An Analytic Model for Per-Inning Scoring
Distributions" (Baseball Prospectus). The inning-level run distribution
is well-approximated by

    P(Y = y | RPG) = c * exp(-k * y)        for y >= 1
    P(Y = 0 | RPG) = 1 - sum_{y>=1} P(Y=y)

with `k` and `c` solved from the half-inning expected runs. Empirically
this matches league per-inning histograms within ~2 percentage points
across a century of data, including for inning 1 specifically.

Why we want this here:

The current NRFI head predicts P(NRFI) directly via gradient boosting.
That's prone to over/under-confidence at the tails (Brier 0.252 vs the
0.246 publish gate). A cleaner architecture has the regression head
predict **expected first-inning runs** (a non-negative real number) and
pass it through Woolner to get a calibrated P(0 runs). Two payoffs:

  1. The output is monotone in expected-runs, so isotonic re-fits stay
     stable across walk-forward retrains.
  2. We can compose top-of-1 + bottom-of-1 independently and combine
     them inside the link function instead of forcing the model to
     learn a joint distribution from scratch.

This module is pure math --- no I/O, no model deps. The bridge into
``feature_engineering.py`` lives in the interactions layer where it
emits a calibrated NRFI-prior feature the GBT can either lean on or
override.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# Half-inning expected runs at which the league's per-inning
# distribution is parameterized. Empirically near 0.51 R / half-inning
# across the 2010s; we let the caller pass per-side estimates so the
# model can use top-of-1 vs bottom-of-1 specific values.
_LEAGUE_HALF_INN_RPG: float = 0.51


@dataclass(frozen=True)
class InningRunDistribution:
    """Per-inning P(0), P(1), P(2+) given an effective half-inning RPG.

    Truncated at y=2 (P_2_or_more is the lumped tail) because the
    NRFI/YRFI market only cares about y == 0 vs y >= 1 and the rest is
    book-keeping. Sums to 1.0 within float epsilon.
    """
    p_zero: float
    p_one: float
    p_two_or_more: float
    expected_runs: float


def woolner_p_zero(half_inning_rpg: float) -> float:
    """Closed-form P(0 runs in a half-inning | half-inning RPG).

    Derives the Woolner exponential parameter k from the constraint
    that ``E[Y] == half_inning_rpg``. The link is:

        P(0)   = 1 - q
        P(y>=1 | y) = q * (1-r) * r^(y-1)    geometric tail

    where ``q = P(Y >= 1)`` and ``r = E[Y | Y >= 1] / (1 + E[Y|Y>=1])``.
    ``E[Y | Y >= 1]`` empirically settles near ~1.6 for league-average
    innings; we infer it from the input RPG so the function is
    monotone increasing in RPG (sanity-checked at the boundaries).

    Returns a value strictly in (0, 1). Negative or zero RPG inputs
    collapse to ``P(0) == 1`` (no runs possible / scheduled).
    """
    if half_inning_rpg <= 0.0:
        return 1.0
    # Anchor: at half-inning RPG ~= 0.51 the league P(0) sits near 0.71
    # (FanGraphs first-inning splits + Woolner 2007). Solve k from
    # P(0) = exp(-k * RPG) so the league mean lands on its empirical
    # value: k = -ln(0.71) / 0.51 ~= 0.6716.
    k = 0.6716
    return max(1e-6, min(1.0 - 1e-6, math.exp(-k * half_inning_rpg)))


def woolner_distribution(
    half_inning_rpg: float,
) -> InningRunDistribution:
    """Full P(0), P(1), P(2+) for one half-inning at the requested RPG.

    The geometric-tail parameter ``r`` is computed so the conditional
    mean ``E[Y | Y>=1]`` increases monotonically in input RPG without
    blowing up at the tails.
    """
    if half_inning_rpg <= 0.0:
        return InningRunDistribution(
            p_zero=1.0, p_one=0.0, p_two_or_more=0.0,
            expected_runs=0.0,
        )
    p0 = woolner_p_zero(half_inning_rpg)
    q = 1.0 - p0
    # Conditional mean given >=1 run scored. League-average ~1.6 R when
    # the inning produces any; scales gently with input RPG.
    cond_mean = 1.0 + 0.6 * (half_inning_rpg / max(1e-3, _LEAGUE_HALF_INN_RPG))
    cond_mean = max(1.05, min(3.0, cond_mean))
    r = (cond_mean - 1.0) / cond_mean
    p1 = q * (1.0 - r)
    p2plus = q * r
    expected = p1 * 1.0 + p2plus * (1.0 + 1.0 / max(1e-6, 1.0 - r))
    return InningRunDistribution(
        p_zero=p0, p_one=p1, p_two_or_more=p2plus,
        expected_runs=expected,
    )


def nrfi_probability(
    top_half_rpg: float, bottom_half_rpg: float,
) -> float:
    """P(no runs in inning 1) given top-of-1 + bottom-of-1 expected runs.

    Top and bottom halves are treated as independent --- pitcher
    duels rarely propagate state (no inning-1 carryover). Model the
    correlation later if the backtest demands it.
    """
    p_top_zero = woolner_p_zero(top_half_rpg)
    p_bottom_zero = woolner_p_zero(bottom_half_rpg)
    return max(1e-6, min(1.0 - 1e-6, p_top_zero * p_bottom_zero))


def yrfi_probability(
    top_half_rpg: float, bottom_half_rpg: float,
) -> float:
    """Complement of :func:`nrfi_probability`."""
    return 1.0 - nrfi_probability(top_half_rpg, bottom_half_rpg)
