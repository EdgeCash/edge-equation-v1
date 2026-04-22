"""
Expected Calibration Error (ECE) + Maximum Calibration Error (MCE).

Definitions (Naeini et al., Guo et al.):

    ECE = sum_k (n_k / n) * |mean_pred_k - mean_outcome_k|
    MCE = max_k |mean_pred_k - mean_outcome_k|

Both metrics consume the same equal-width reliability bins the Phase 7b
backtest.calibration module already produces. This module stays thin -- it
walks the bins and summarizes -- so it plays well with our existing
CalibrationResult dataclass instead of recomputing binning.

Also exposes a refined Brier view:
- brier_base        -> mean squared error (uncertainty - resolution + reliability)
- reliability_frac  -> reliability / brier_base  (how much of Brier is miscalibration)
- resolution_frac   -> resolution  / brier_base  (how much is signal)
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from edge_equation.backtest.calibration import Calibration, CalibrationResult, ReliabilityBin


@dataclass(frozen=True)
class CalibrationMetrics:
    """Extended calibration summary on top of Phase 7b's CalibrationResult."""
    ece: Decimal
    mce: Decimal
    brier: Decimal
    log_loss: Decimal
    reliability_frac: Decimal
    resolution_frac: Decimal
    n: int

    def to_dict(self) -> dict:
        return {
            "ece": str(self.ece),
            "mce": str(self.mce),
            "brier": str(self.brier),
            "log_loss": str(self.log_loss),
            "reliability_frac": str(self.reliability_frac),
            "resolution_frac": str(self.resolution_frac),
            "n": self.n,
        }


class CalibrationAnalytics:
    """
    Summarize a CalibrationResult into ECE / MCE / reliability fractions:
    - ece(result)                 -> Decimal
    - mce(result)                 -> Decimal
    - compute(preds, outcomes, n_bins=10) -> CalibrationMetrics
    - from_result(result)         -> CalibrationMetrics   (no recompute)
    """

    @staticmethod
    def ece(result: CalibrationResult) -> Decimal:
        n = result.n
        if n <= 0:
            return Decimal('0').quantize(Decimal('0.000001'))
        total = Decimal('0')
        for b in result.bins:
            if b.count == 0:
                continue
            gap = abs(b.mean_pred - b.mean_outcome)
            total += Decimal(b.count) * gap
        return (total / Decimal(n)).quantize(Decimal('0.000001'))

    @staticmethod
    def mce(result: CalibrationResult) -> Decimal:
        best = Decimal('0')
        for b in result.bins:
            if b.count == 0:
                continue
            gap = abs(b.mean_pred - b.mean_outcome)
            if gap > best:
                best = gap
        return best.quantize(Decimal('0.000001'))

    @staticmethod
    def from_result(result: CalibrationResult) -> CalibrationMetrics:
        brier = result.brier
        if brier <= Decimal('0'):
            reliability_frac = Decimal('0').quantize(Decimal('0.000001'))
            resolution_frac = Decimal('0').quantize(Decimal('0.000001'))
        else:
            reliability_frac = (result.reliability / brier).quantize(Decimal('0.000001'))
            resolution_frac = (result.resolution / brier).quantize(Decimal('0.000001'))
        return CalibrationMetrics(
            ece=CalibrationAnalytics.ece(result),
            mce=CalibrationAnalytics.mce(result),
            brier=brier,
            log_loss=result.log_loss,
            reliability_frac=reliability_frac,
            resolution_frac=resolution_frac,
            n=result.n,
        )

    @staticmethod
    def compute(
        preds: Iterable,
        outcomes: Iterable,
        n_bins: int = 10,
    ) -> CalibrationMetrics:
        result = Calibration.compute(preds, outcomes, n_bins=n_bins)
        return CalibrationAnalytics.from_result(result)
