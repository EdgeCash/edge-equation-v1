"""
Quantile-based edge grading.

Given a historical distribution of edges, fit quantile thresholds that map
any future edge to one of A, B, C, D, F. Default quantiles:
  A: >= 80th percentile
  B: >= 60th percentile
  C: >= 40th percentile
  D: >= 20th percentile
  F: below 20th percentile

The A+ band is optional: if A_PLUS_QUANTILE is supplied at fit time, edges at
or above that quantile receive 'A+'.

This complements (does not replace) math/scoring.ConfidenceScorer, which uses
fixed edge thresholds. GradeCalibrator is the data-driven alternative.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List, Optional


DEFAULT_A_QUANTILE = Decimal('0.80')
DEFAULT_B_QUANTILE = Decimal('0.60')
DEFAULT_C_QUANTILE = Decimal('0.40')
DEFAULT_D_QUANTILE = Decimal('0.20')


@dataclass(frozen=True)
class GradeThresholds:
    """Edge-value thresholds fit from a historical distribution."""
    a_plus: Optional[Decimal]
    a: Decimal
    b: Decimal
    c: Decimal
    d: Decimal
    n_fit: int

    def to_dict(self) -> dict:
        return {
            "a_plus": str(self.a_plus) if self.a_plus is not None else None,
            "a": str(self.a),
            "b": str(self.b),
            "c": str(self.c),
            "d": str(self.d),
            "n_fit": self.n_fit,
        }


class GradeCalibrator:
    """
    Quantile-based grading:
    - fit(edges, a_plus_quantile=None) -> GradeThresholds
    - grade(edge, thresholds) -> one of 'A+', 'A', 'B', 'C', 'D', 'F'
    Quantiles computed via linear interpolation between adjacent sorted values
    (same convention as numpy's default 'linear' method).
    """

    @staticmethod
    def _quantile(sorted_vals: List[Decimal], q: Decimal) -> Decimal:
        n = len(sorted_vals)
        if n == 1:
            return sorted_vals[0]
        pos = q * Decimal(n - 1)
        lo = int(pos)
        hi = lo + 1 if lo < n - 1 else lo
        frac = pos - Decimal(lo)
        if hi == lo:
            return sorted_vals[lo]
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac

    @staticmethod
    def fit(
        edges: Iterable[float],
        a_plus_quantile: Optional[Decimal] = None,
    ) -> GradeThresholds:
        vals = sorted(Decimal(str(e)) for e in edges)
        n = len(vals)
        if n == 0:
            raise ValueError("cannot fit thresholds on empty edge distribution")
        a_plus = None
        if a_plus_quantile is not None:
            if a_plus_quantile <= DEFAULT_A_QUANTILE or a_plus_quantile >= Decimal('1'):
                raise ValueError(
                    f"a_plus_quantile must be in ({DEFAULT_A_QUANTILE}, 1), got {a_plus_quantile}"
                )
            a_plus = GradeCalibrator._quantile(vals, a_plus_quantile).quantize(Decimal('0.000001'))
        return GradeThresholds(
            a_plus=a_plus,
            a=GradeCalibrator._quantile(vals, DEFAULT_A_QUANTILE).quantize(Decimal('0.000001')),
            b=GradeCalibrator._quantile(vals, DEFAULT_B_QUANTILE).quantize(Decimal('0.000001')),
            c=GradeCalibrator._quantile(vals, DEFAULT_C_QUANTILE).quantize(Decimal('0.000001')),
            d=GradeCalibrator._quantile(vals, DEFAULT_D_QUANTILE).quantize(Decimal('0.000001')),
            n_fit=n,
        )

    @staticmethod
    def grade(edge: Decimal, thresholds: GradeThresholds) -> str:
        if thresholds.a_plus is not None and edge >= thresholds.a_plus:
            return "A+"
        if edge >= thresholds.a:
            return "A"
        if edge >= thresholds.b:
            return "B"
        if edge >= thresholds.c:
            return "C"
        if edge >= thresholds.d:
            return "D"
        return "F"
