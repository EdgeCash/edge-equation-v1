"""
Monte Carlo simulator for fair-probability uncertainty.

The engine's ProbabilityCalculator produces a POINT ESTIMATE of the
fair win probability from point-estimate inputs (strength_home,
strength_away, home_adv). What MVS (Major Variance Signal) actually
needs is the SAMPLING DISTRIBUTION of that fair_prob given reasonable
uncertainty on the inputs -- the real question is "if our strength
estimates are off by a bit, how much does fair_prob shift?"

This module answers that by perturbing each input with a small
Gaussian (deterministic, seeded) and re-running Bradley-Terry 10,000
times. The output is the distribution of fair_probs we'd see if our
inputs drifted within their credible region.

Facts Not Feelings: this is uncertainty quantification, NOT a
trader's Monte Carlo of outcomes. Every number is deterministic from
a seed so two runs on the same inputs produce identical stability
metrics -- critical for reproducibility.

The detector (engine/major_variance.py) consumes the stdev + p10/p90
keys returned here. Low stdev + tight band = stable projection =
meets the MVS stability bar. High stdev = input-uncertainty too large
for the model to warrant an MVS flag.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional


# Default perturbation stdev on team-strength inputs. Chosen so
# a typical BT evaluation's fair_prob stdev lands in the 0.03 - 0.08
# range -- that's the uncertainty band the MVS detector reads at.
# Tighter (0.02) = more optimistic about the engine, looser (0.10)
# = more conservative. 0.05 is the "credible region for a team
# with N>=30 settled games" default; teams with thinner history
# should use a wider prior via strength_prior_sigma.
DEFAULT_STRENGTH_SIGMA = 0.05
# Default run size. 10k is the brand commitment ("10,000 EV
# simulations") referenced on the AI graphic footer.
DEFAULT_N_SIMS = 10_000


@dataclass(frozen=True)
class MCResult:
    """Stability metrics over a sampled fair-probability distribution."""
    mean: Decimal
    stdev: Decimal
    p10: Decimal
    p50: Decimal
    p90: Decimal
    n: int

    def to_dict(self) -> dict:
        # Keys match what detect_major_variance() reads.
        return {
            "mean": str(self.mean),
            "stdev": str(self.stdev),
            "p10": str(self.p10),
            "p50": str(self.p50),
            "p90": str(self.p90),
            "n": self.n,
        }


def _seed_from(*parts: object) -> int:
    """Derive a 32-bit deterministic seed from string-able parts.
    Same game_id + same inputs -> same seed -> identical MC stats
    across runs (critical for auditability)."""
    m = hashlib.sha256()
    for p in parts:
        m.update(str(p).encode("utf-8"))
        m.update(b"|")
    return int.from_bytes(m.digest()[:4], "big")


def _quantize(x: float, digits: int = 4) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("1").scaleb(-digits))


def _percentile(sorted_values, q: float) -> float:
    """Linear-interpolation percentile over a pre-sorted list. Avoids
    a numpy dependency."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = q * (len(sorted_values) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_values[lo]
    frac = idx - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _bradley_terry(strength_home: float, strength_away: float, home_adv: float) -> float:
    """Plain-float Bradley-Terry (no Decimal overhead inside the
    hot-path MC loop). Clamped to [0.001, 0.999] to avoid degenerate
    log-odds later."""
    home = strength_home * math.exp(home_adv)
    away = strength_away
    denom = home + away
    if denom <= 0:
        return 0.5
    p = home / denom
    if p < 0.001: return 0.001
    if p > 0.999: return 0.999
    return p


class MonteCarloSimulator:
    """Deterministic fair-probability uncertainty simulator.

    Two public entry points:
      - simulate_ml(strength_home, strength_away, home_adv, seed_key)
      - simulate_point_prob(fair_prob, sigma, seed_key)

    Both return an MCResult carrying mean/stdev/p10/p50/p90 suitable
    for the MVS detector.
    """

    @staticmethod
    def simulate_ml(
        strength_home: float,
        strength_away: float,
        home_adv: float,
        seed_key: str = "",
        n_sims: int = DEFAULT_N_SIMS,
        strength_sigma: float = DEFAULT_STRENGTH_SIGMA,
    ) -> MCResult:
        """MC over Bradley-Terry strengths. Perturbs each team's
        strength with an independent Gaussian of the given sigma
        (multiplicative, clamped > 0.01), re-runs BT, collects the
        distribution of fair_probs.
        """
        rng = random.Random(_seed_from("ml", seed_key,
                                       strength_home, strength_away,
                                       home_adv, n_sims, strength_sigma))
        samples = []
        for _ in range(n_sims):
            # Multiplicative log-normal perturbation so strengths stay
            # strictly positive. stdev of ln-strength ~ sigma.
            sh = max(0.01, strength_home * math.exp(rng.gauss(0.0, strength_sigma)))
            sa = max(0.01, strength_away * math.exp(rng.gauss(0.0, strength_sigma)))
            # home_adv is a league-level constant; leave un-perturbed
            # (its uncertainty is baked into strength_sigma already).
            samples.append(_bradley_terry(sh, sa, home_adv))
        return MonteCarloSimulator._stats(samples)

    @staticmethod
    def simulate_point_prob(
        fair_prob: Decimal,
        seed_key: str = "",
        n_sims: int = DEFAULT_N_SIMS,
        prob_sigma: float = 0.04,
    ) -> MCResult:
        """Fallback for markets where we have a point-probability
        (e.g., BTTS, Poisson-derived first-inning markets) but no
        Bradley-Terry inputs. Gaussian perturbation in logit space
        so samples stay in (0, 1)."""
        rng = random.Random(_seed_from("point", seed_key, str(fair_prob), n_sims, prob_sigma))
        p = float(fair_prob)
        # Clamp so logit is defined.
        p = max(0.001, min(0.999, p))
        logit_p = math.log(p / (1 - p))
        samples = []
        for _ in range(n_sims):
            l = logit_p + rng.gauss(0.0, prob_sigma)
            # Inverse logit -> prob in (0,1)
            samples.append(1.0 / (1.0 + math.exp(-l)))
        return MonteCarloSimulator._stats(samples)

    @staticmethod
    def _stats(samples) -> MCResult:
        n = len(samples)
        if n == 0:
            zero = Decimal("0.0000")
            return MCResult(
                mean=zero, stdev=zero, p10=zero, p50=zero, p90=zero, n=0,
            )
        mean = sum(samples) / n
        if n > 1:
            var = sum((x - mean) ** 2 for x in samples) / (n - 1)
            stdev = math.sqrt(var)
        else:
            stdev = 0.0
        sorted_s = sorted(samples)
        return MCResult(
            mean=_quantize(mean),
            stdev=_quantize(stdev),
            p10=_quantize(_percentile(sorted_s, 0.10)),
            p50=_quantize(_percentile(sorted_s, 0.50)),
            p90=_quantize(_percentile(sorted_s, 0.90)),
            n=n,
        )
