"""
Negative-binomial rate props.

For a player prop with a mean rate mu and a dispersion parameter alpha
(variance = mu + alpha * mu^2), the distribution of discrete outcomes is
negative binomial:

    Var(X) = mu + alpha * mu^2
    r = 1 / alpha
    p = 1 / (1 + alpha * mu)        (success probability parameterization)

PMF in log space (numerically stable via math.lgamma):

    log P(X = k) = lgamma(k + r)
                 - lgamma(k + 1)
                 - lgamma(r)
                 + r * log(p)
                 + k * log(1 - p)

CDF is the running sum of the PMF from 0..k. For an over/under prop at a
half-integer line (e.g. 6.5), P(Over 6.5) = 1 - CDF(6) and there is no push.
At an integer line (e.g. 6), P(Over 6) = 1 - CDF(6), P(Under 6) = CDF(5),
and P(push on exactly 6) = PMF(6).

Everything below is deterministic Decimal math -- no RNG, no model weights,
no dependencies beyond stdlib.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import math


DEFAULT_ALPHA = Decimal('0.15')  # typical rate-prop dispersion floor


@dataclass(frozen=True)
class NegBinomParams:
    """Immutable (mean, dispersion) pair for a negative-binomial prop."""
    mu: Decimal
    alpha: Decimal

    def r(self) -> Decimal:
        if self.alpha <= Decimal('0'):
            raise ValueError(f"alpha must be > 0, got {self.alpha}")
        return (Decimal('1') / self.alpha).quantize(Decimal('0.000001'))

    def p(self) -> Decimal:
        return (Decimal('1') / (Decimal('1') + self.alpha * self.mu)).quantize(Decimal('0.000001'))

    def variance(self) -> Decimal:
        return (self.mu + self.alpha * self.mu * self.mu).quantize(Decimal('0.000001'))

    def to_dict(self) -> dict:
        return {
            "mu": str(self.mu),
            "alpha": str(self.alpha),
            "r": str(self.r()),
            "p": str(self.p()),
            "variance": str(self.variance()),
        }


@dataclass(frozen=True)
class OverUnderProbs:
    """Outcome probabilities for one (line, side) pair."""
    line: Decimal
    p_over: Decimal
    p_under: Decimal
    p_push: Decimal

    def to_dict(self) -> dict:
        return {
            "line": str(self.line),
            "p_over": str(self.p_over),
            "p_under": str(self.p_under),
            "p_push": str(self.p_push),
        }


class NegativeBinomial:
    """
    Deterministic PMF / CDF + over/under helpers:
    - log_pmf(k, params)
    - pmf(k, params)           -> Decimal
    - cdf(k, params)           -> Decimal  (sum of PMF 0..k)
    - over_under(line, params) -> OverUnderProbs
    - expected_rate(params)    -> Decimal  (== params.mu, for API symmetry)
    """

    @staticmethod
    def log_pmf(k: int, params: NegBinomParams) -> float:
        if k < 0:
            return float("-inf")
        r = float(params.r())
        p = float(params.p())
        if p <= 0.0 or p >= 1.0:
            raise ValueError(f"p out of range: {p}")
        return (
            math.lgamma(k + r)
            - math.lgamma(k + 1)
            - math.lgamma(r)
            + r * math.log(p)
            + k * math.log(1.0 - p)
        )

    @staticmethod
    def pmf(k: int, params: NegBinomParams) -> Decimal:
        if k < 0:
            return Decimal('0').quantize(Decimal('0.000001'))
        return Decimal(str(math.exp(NegativeBinomial.log_pmf(k, params)))).quantize(Decimal('0.000001'))

    @staticmethod
    def cdf(k: int, params: NegBinomParams) -> Decimal:
        if k < 0:
            return Decimal('0').quantize(Decimal('0.000001'))
        # Sum in log-space accumulated into a float, then quantize once at end.
        total = 0.0
        for i in range(0, k + 1):
            total += math.exp(NegativeBinomial.log_pmf(i, params))
        # Guard against float drift above 1.0
        if total > 1.0:
            total = 1.0
        return Decimal(str(total)).quantize(Decimal('0.000001'))

    @staticmethod
    def over_under(line, params: NegBinomParams) -> OverUnderProbs:
        """
        Under/Over/Push split for a sportsbook line. Half-integer lines (6.5,
        9.5, ...) produce a zero push; integer lines (6, 7, ...) produce a
        non-zero push equal to PMF(line).
        """
        line_dec = line if isinstance(line, Decimal) else Decimal(str(line))
        # A line of 6.5 means: Over 6.5 covers {7, 8, ...} = 1 - CDF(6).
        # A line of 6   means: Over 6   covers {7, 8, ...} = 1 - CDF(6).
        integer_floor = int(math.floor(float(line_dec)))
        is_half_integer = (line_dec != Decimal(integer_floor))
        cdf_at_floor = NegativeBinomial.cdf(integer_floor, params)
        p_over = (Decimal('1') - cdf_at_floor).quantize(Decimal('0.000001'))

        if is_half_integer:
            p_push = Decimal('0').quantize(Decimal('0.000001'))
            p_under = cdf_at_floor.quantize(Decimal('0.000001'))
        else:
            p_push = NegativeBinomial.pmf(integer_floor, params)
            # Under excludes the push cell.
            cdf_below = NegativeBinomial.cdf(integer_floor - 1, params)
            p_under = cdf_below.quantize(Decimal('0.000001'))

        # Renormalize against rounding drift so p_over + p_under + p_push = 1 exactly.
        total = p_over + p_under + p_push
        if total > Decimal('0'):
            p_over = (p_over / total).quantize(Decimal('0.000001'))
            p_under = (p_under / total).quantize(Decimal('0.000001'))
            p_push = (p_push / total).quantize(Decimal('0.000001'))

        return OverUnderProbs(
            line=line_dec,
            p_over=p_over,
            p_under=p_under,
            p_push=p_push,
        )

    @staticmethod
    def expected_rate(params: NegBinomParams) -> Decimal:
        return params.mu.quantize(Decimal('0.000001'))
