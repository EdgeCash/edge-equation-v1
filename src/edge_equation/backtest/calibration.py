"""
Probabilistic calibration metrics.

Brier score:
  B = (1/n) * sum((p_i - y_i)^2)   for y_i in {0, 1}

Log loss (base e), with clamping to avoid log(0):
  L = -(1/n) * sum(y_i*log(p_i) + (1-y_i)*log(1-p_i))

Murphy (reliability-resolution-uncertainty) decomposition:
  B = Reliability - Resolution + Uncertainty
  Uncertainty = p̄ * (1 - p̄)                         # base rate variance
  Resolution  = (1/n) * sum_k n_k * (p̄_k - p̄)^2      # how much bins differ from base
  Reliability = (1/n) * sum_k n_k * (f̄_k - p̄_k)^2    # miscalibration within bins
where f̄_k is the mean predicted prob in bin k and p̄_k is the mean outcome in
bin k. Equal-width bins on [0, 1].
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, List, Tuple
import math


CLAMP_EPS = Decimal('0.000001')  # clip probabilities away from 0/1 for log loss


@dataclass(frozen=True)
class ReliabilityBin:
    """One equal-width reliability bin summary."""
    bin_start: Decimal
    bin_end: Decimal
    mean_pred: Decimal
    mean_outcome: Decimal
    count: int

    def to_dict(self) -> dict:
        return {
            "bin_start": str(self.bin_start),
            "bin_end": str(self.bin_end),
            "mean_pred": str(self.mean_pred),
            "mean_outcome": str(self.mean_outcome),
            "count": self.count,
        }


@dataclass(frozen=True)
class CalibrationResult:
    """Full calibration report including Murphy decomposition and bins."""
    brier: Decimal
    log_loss: Decimal
    reliability: Decimal
    resolution: Decimal
    uncertainty: Decimal
    bins: Tuple[ReliabilityBin, ...] = field(default_factory=tuple)
    n: int = 0

    def to_dict(self) -> dict:
        return {
            "brier": str(self.brier),
            "log_loss": str(self.log_loss),
            "reliability": str(self.reliability),
            "resolution": str(self.resolution),
            "uncertainty": str(self.uncertainty),
            "bins": [b.to_dict() for b in self.bins],
            "n": self.n,
        }


class Calibration:
    """
    Calibration metrics for a vector of predicted probabilities vs. binary outcomes:
    - brier(preds, outcomes)       -> Decimal
    - log_loss(preds, outcomes)    -> Decimal (natural log, clamped)
    - compute(preds, outcomes, n_bins=10) -> CalibrationResult with Murphy decomp
    Inputs must be equal length; outcomes in {0, 1}; preds in [0, 1].
    """

    @staticmethod
    def _validate(preds, outcomes) -> tuple:
        p = [Decimal(str(x)) for x in preds]
        y = [int(v) for v in outcomes]
        if len(p) != len(y):
            raise ValueError(f"preds and outcomes must have equal length, got {len(p)} vs {len(y)}")
        if not p:
            raise ValueError("empty input")
        for v in y:
            if v not in (0, 1):
                raise ValueError(f"outcomes must be 0 or 1, got {v}")
        for x in p:
            if x < Decimal('0') or x > Decimal('1'):
                raise ValueError(f"preds must be in [0, 1], got {x}")
        return p, y

    @staticmethod
    def brier(preds: Iterable[float], outcomes: Iterable[int]) -> Decimal:
        p, y = Calibration._validate(preds, outcomes)
        n = len(p)
        total = sum((p[i] - Decimal(y[i])) ** 2 for i in range(n))
        return (total / Decimal(n)).quantize(Decimal('0.000001'))

    @staticmethod
    def log_loss(preds: Iterable[float], outcomes: Iterable[int]) -> Decimal:
        p, y = Calibration._validate(preds, outcomes)
        n = len(p)
        total = Decimal('0')
        for i in range(n):
            pi = p[i]
            if pi < CLAMP_EPS:
                pi = CLAMP_EPS
            elif pi > Decimal('1') - CLAMP_EPS:
                pi = Decimal('1') - CLAMP_EPS
            if y[i] == 1:
                total += Decimal(str(math.log(float(pi))))
            else:
                total += Decimal(str(math.log(float(Decimal('1') - pi))))
        return (-total / Decimal(n)).quantize(Decimal('0.000001'))

    @staticmethod
    def _bin_index(prob: Decimal, n_bins: int) -> int:
        if prob >= Decimal('1'):
            return n_bins - 1
        step = Decimal('1') / Decimal(n_bins)
        idx = int(prob / step)
        if idx >= n_bins:
            idx = n_bins - 1
        return idx

    @staticmethod
    def compute(
        preds: Iterable[float],
        outcomes: Iterable[int],
        n_bins: int = 10,
    ) -> CalibrationResult:
        if n_bins < 1:
            raise ValueError(f"n_bins must be >= 1, got {n_bins}")
        p, y = Calibration._validate(preds, outcomes)
        n = len(p)

        brier = Calibration.brier(preds, outcomes)
        log_loss = Calibration.log_loss(preds, outcomes)

        base_rate = Decimal(sum(y)) / Decimal(n)
        uncertainty = (base_rate * (Decimal('1') - base_rate)).quantize(Decimal('0.000001'))

        # Accumulate per-bin sums
        pred_sum = [Decimal('0')] * n_bins
        outcome_sum = [Decimal('0')] * n_bins
        counts = [0] * n_bins
        for i in range(n):
            k = Calibration._bin_index(p[i], n_bins)
            pred_sum[k] += p[i]
            outcome_sum[k] += Decimal(y[i])
            counts[k] += 1

        step = Decimal('1') / Decimal(n_bins)
        bins: List[ReliabilityBin] = []
        resolution_acc = Decimal('0')
        reliability_acc = Decimal('0')
        for k in range(n_bins):
            start = (step * Decimal(k)).quantize(Decimal('0.000001'))
            end = (step * Decimal(k + 1)).quantize(Decimal('0.000001'))
            if counts[k] == 0:
                mean_pred = Decimal('0').quantize(Decimal('0.000001'))
                mean_outcome = Decimal('0').quantize(Decimal('0.000001'))
            else:
                mean_pred = (pred_sum[k] / Decimal(counts[k])).quantize(Decimal('0.000001'))
                mean_outcome = (outcome_sum[k] / Decimal(counts[k])).quantize(Decimal('0.000001'))
                resolution_acc += Decimal(counts[k]) * (mean_outcome - base_rate) ** 2
                reliability_acc += Decimal(counts[k]) * (mean_pred - mean_outcome) ** 2
            bins.append(ReliabilityBin(
                bin_start=start,
                bin_end=end,
                mean_pred=mean_pred,
                mean_outcome=mean_outcome,
                count=counts[k],
            ))

        resolution = (resolution_acc / Decimal(n)).quantize(Decimal('0.000001'))
        reliability = (reliability_acc / Decimal(n)).quantize(Decimal('0.000001'))

        return CalibrationResult(
            brier=brier,
            log_loss=log_loss,
            reliability=reliability,
            resolution=resolution,
            uncertainty=uncertainty,
            bins=tuple(bins),
            n=n,
        )
