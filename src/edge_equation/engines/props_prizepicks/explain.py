"""Props explanations — MC bands + decomposition Why notes.

Two helpers consumed by the daily orchestrator to populate the
``PropOutput`` payload's audit fields. Pure-Python, no extras
required (the projection layer already imports nothing beyond stdlib
+ the engine config).

* ``poisson_mc_band`` — sample from Poisson(λ) to get a low/high
  confidence interval on the side's probability. Mirrors NRFI's
  ``mc_band_pp`` UX: the operator sees how wide the model's
  posterior is, not just the point estimate.

* ``decomposition_drivers`` — produces a short list of human-readable
  "Why" bullets explaining what drove the projection. The Poisson
  model is deterministic so we can decompose exactly: how much of
  the projected λ came from the league prior vs the player's own
  rolling rate, scaled by expected volume. No SHAP library needed.

The functions take the ``ProjectedSide`` already produced by
``projection.project_all`` and write back the audit fields the
``PropOutput`` factory consumes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

from .projection import (
    ProjectedSide, _poisson_cdf, _prob_over_poisson,
)


# ---------------------------------------------------------------------------
# MC bands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCBand:
    """Monte-Carlo confidence band for a projected probability.

    Both endpoints are in [0, 1]. The convention matches NRFI's MC
    output: ``low``/``high`` are the 5th/95th percentiles of the
    bootstrap distribution of the side's probability.
    """
    low: float
    high: float
    n_samples: int

    @property
    def band_pp(self) -> float:
        """Width of the band in percentage points (0..100)."""
        return round(max(0.0, self.high - self.low) * 100.0, 1)


def poisson_mc_band(
    side: ProjectedSide, *,
    n_samples: int = 2000,
    rate_jitter: float = 0.10,
    seed: int = 42,
) -> MCBand:
    """Bootstrap a 5/95 confidence band around the side's probability.

    The Poisson model has two sources of uncertainty: (a) which Poisson
    rate is *correct* (the per-player rate has finite-sample noise),
    and (b) which realisation gets drawn from that Poisson. We sample
    both:

    1. Perturb λ by a multiplicative log-normal jitter (default
       σ = 10%) to model "we got the rate slightly wrong."
    2. For each jittered λ, compute ``P(X > line)`` analytically
       (closed-form Poisson CDF — no need to sample integer outcomes).
    3. Take the 5th/95th percentile of the resulting probabilities.

    The jitter scale is intentionally modest. The point of the band is
    to surface "this projection is steady" vs "this projection is
    fragile to small rate errors" — not to re-derive a full posterior.
    """
    rng = random.Random(seed)
    lam = max(0.0, float(side.lam))
    line = float(side.line_value)
    side_lower = (side.side or "").strip().lower()
    if lam == 0.0 or n_samples <= 0:
        return MCBand(low=side.model_prob, high=side.model_prob, n_samples=0)

    # log-normal multiplicative jitter so λ stays positive
    samples: list[float] = []
    for _ in range(n_samples):
        jitter = math.exp(rng.gauss(0.0, rate_jitter))
        lam_j = lam * jitter
        p_over = _prob_over_poisson(line, lam_j)
        if side_lower in ("under", "no"):
            samples.append(1.0 - p_over)
        else:
            samples.append(p_over)
    samples.sort()
    lo_idx = max(0, int(0.05 * len(samples)) - 1)
    hi_idx = min(len(samples) - 1, int(0.95 * len(samples)))
    return MCBand(
        low=round(samples[lo_idx], 4),
        high=round(samples[hi_idx], 4),
        n_samples=n_samples,
    )


# ---------------------------------------------------------------------------
# Decomposition — "Why" bullets
# ---------------------------------------------------------------------------


def decomposition_drivers(
    side: ProjectedSide,
    *,
    league_prior_rate: float,
    expected_volume: float,
    prior_weight: float,
    market_prob: float | None = None,
    edge_pp: float | None = None,
) -> list[str]:
    """Build short "Why" bullets explaining what drove the projection.

    The Poisson projection is exact: λ = rate × volume, where the
    blended rate = (own_weight × own_rate) + ((1−own_weight) × prior).
    We expose those terms in plain language so the operator (and any
    public reader) can see whether the call rests on the player's
    actual recent form or on the league prior.

    Bullets returned (most to least significant):

    1. **Rate split** — "78% own form / 22% league prior" so the
       reader knows whether the call leans on signal or the fallback.
    2. **Volume × rate breakdown** — "blended 0.275/PA × 4.1 PA → λ 1.13"
       so a curious reader can run the math themselves.
    3. **Edge framing** (only when market_prob is provided) — keeps
       the reader oriented to *why this is a bet*, not just a forecast.

    We deliberately don't go beyond 3-4 bullets. The driver list ends
    up in the email card and the API payload; verbosity hurts both.
    """
    bullets: list[str] = []

    # 1. Own-rate vs prior split
    n = max(0, int(side.blend_n))
    pw = max(1e-6, float(prior_weight))
    own_weight = n / (n + pw) if n > 0 else 0.0
    prior_weight_share = 1.0 - own_weight
    own_pct = round(own_weight * 100.0)
    prior_pct = round(prior_weight_share * 100.0)
    if n == 0:
        bullets.append(
            f"No own-rate data yet — projection rests entirely on the "
            f"league prior ({_pretty_rate(league_prior_rate)}/unit)."
        )
    elif own_pct >= 70:
        bullets.append(
            f"{own_pct}% weight on player's own recent form "
            f"({n} samples) vs {prior_pct}% league prior."
        )
    elif own_pct >= 35:
        bullets.append(
            f"Balanced read: {own_pct}% own form ({n} samples), "
            f"{prior_pct}% league prior."
        )
    else:
        bullets.append(
            f"Thin sample ({n}) — projection still leans {prior_pct}% "
            f"toward the league prior."
        )

    # 2. λ build-up
    blended_rate = float(side.blended_rate or 0.0)
    if expected_volume > 0 and blended_rate > 0:
        bullets.append(
            f"Rate {blended_rate:.3f}/unit × {expected_volume:.1f} expected "
            f"→ λ {side.lam:.2f}."
        )

    # 3. Edge framing
    if edge_pp is not None and market_prob is not None and 0.0 < market_prob < 1.0:
        sign = "+" if edge_pp >= 0 else ""
        bullets.append(
            f"Model {round(side.model_prob * 100, 1)}% vs market "
            f"{round(market_prob * 100, 1)}% → {sign}{edge_pp:.1f}pp edge."
        )

    return bullets


def _pretty_rate(r: float) -> str:
    """Format a rate so 0.235 reads as '23.5%' but 4.1 reads as '4.1'."""
    if 0.0 < r < 1.0:
        return f"{r * 100:.1f}%"
    return f"{r:.2f}"
