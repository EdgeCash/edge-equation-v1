"""
Adaptive Kelly with multiplicative shrinkage.

The standard Kelly fraction is shrunk by four independent factors that each
live in [0, 1], multiplied together, then scaled by BASE_FRACTION and clamped
to the per-bet cap. A separate daily cap trims a candidate allocation so that
the running sum of Kelly allocations over the day never exceeds DAILY_CAP.

Shrinkage stack:
  1. uncertainty:    e^2 / (e^2 + sigma^2)
  2. sample-size:    n / (n + N_PRIOR)                  (N_PRIOR = 30)
  3. portfolio:      1 / (1 + PORT_ALPHA * (k - 1))     (k = portfolio_size)
  4. correlation:    1 - max_sibling_corr

Final allocation:
  kelly_final = min(
    BASE_FRACTION * full_kelly * uncertainty * sample_size * portfolio * correlation,
    PER_BET_CAP
  )

Edge floor: if edge < EDGE_FLOOR, allocation is 0.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional


BASE_FRACTION = Decimal('0.25')
EDGE_FLOOR = Decimal('0.02')
PER_BET_CAP = Decimal('0.05')
DAILY_CAP = Decimal('0.25')
N_PRIOR = 30
PORT_ALPHA = Decimal('0.5')


@dataclass(frozen=True)
class KellyInputs:
    """
    Inputs for a single adaptive-Kelly computation.

    edge:              fair_prob - implied_prob
    decimal_odds:      European decimal odds (>1)
    fair_prob_stderr:  sigma on fair_prob (for uncertainty shrinkage); 0 disables
    sample_size:       n observations behind the estimate; 0 yields zero allocation
    portfolio_size:    k bets in the current slate (>=1)
    max_sibling_corr:  max pairwise correlation with sibling bets in the slate
    """
    edge: Decimal
    decimal_odds: Decimal
    fair_prob_stderr: Decimal = Decimal('0')
    sample_size: int = 0
    portfolio_size: int = 1
    max_sibling_corr: Decimal = Decimal('0')


@dataclass(frozen=True)
class KellyResult:
    """Resolved adaptive Kelly with per-factor breakdown for auditability."""
    full_kelly: Decimal
    uncertainty_factor: Decimal
    sample_factor: Decimal
    portfolio_factor: Decimal
    correlation_factor: Decimal
    base_fraction: Decimal
    pre_cap: Decimal
    kelly_final: Decimal
    capped: bool

    def to_dict(self) -> dict:
        return {
            "full_kelly": str(self.full_kelly),
            "uncertainty_factor": str(self.uncertainty_factor),
            "sample_factor": str(self.sample_factor),
            "portfolio_factor": str(self.portfolio_factor),
            "correlation_factor": str(self.correlation_factor),
            "base_fraction": str(self.base_fraction),
            "pre_cap": str(self.pre_cap),
            "kelly_final": str(self.kelly_final),
            "capped": self.capped,
        }


class AdaptiveKelly:
    """
    Adaptive Kelly sizing:
    - compute(inputs) -> KellyResult with multiplicative shrinkage stack
    - apply_daily_cap(candidate, running_total) truncates so total <= DAILY_CAP
    - hard per-bet cap PER_BET_CAP; edge floor EDGE_FLOOR zeros allocation below
    """

    @staticmethod
    def _uncertainty_factor(edge: Decimal, sigma: Decimal) -> Decimal:
        if sigma <= Decimal('0'):
            return Decimal('1').quantize(Decimal('0.000001'))
        e2 = edge * edge
        s2 = sigma * sigma
        denom = e2 + s2
        if denom == Decimal('0'):
            return Decimal('0').quantize(Decimal('0.000001'))
        return (e2 / denom).quantize(Decimal('0.000001'))

    @staticmethod
    def _sample_factor(n: int) -> Decimal:
        if n <= 0:
            return Decimal('0').quantize(Decimal('0.000001'))
        nd = Decimal(n)
        return (nd / (nd + Decimal(N_PRIOR))).quantize(Decimal('0.000001'))

    @staticmethod
    def _portfolio_factor(k: int) -> Decimal:
        if k < 1:
            raise ValueError(f"portfolio_size must be >= 1, got {k}")
        kd = Decimal(k)
        return (Decimal('1') / (Decimal('1') + PORT_ALPHA * (kd - Decimal('1')))).quantize(Decimal('0.000001'))

    @staticmethod
    def _correlation_factor(max_corr: Decimal) -> Decimal:
        if max_corr < Decimal('0'):
            max_corr = Decimal('0')
        if max_corr > Decimal('1'):
            max_corr = Decimal('1')
        return (Decimal('1') - max_corr).quantize(Decimal('0.000001'))

    @staticmethod
    def _full_kelly(edge: Decimal, decimal_odds: Decimal) -> Decimal:
        if decimal_odds <= Decimal('1'):
            return Decimal('0').quantize(Decimal('0.000001'))
        k = edge / (decimal_odds - Decimal('1'))
        if k < Decimal('0'):
            return Decimal('0').quantize(Decimal('0.000001'))
        return k.quantize(Decimal('0.000001'))

    @staticmethod
    def from_mc(
        edge: Decimal,
        decimal_odds: Decimal,
        mc_result: dict,
        sample_size: int = 0,
        portfolio_size: int = 1,
        max_sibling_corr: Decimal = Decimal('0'),
    ) -> "KellyResult":
        """
        Convenience constructor: pull a standard-deviation-as-stderr out of a
        MonteCarloSimulator result dict and feed it into compute(). MC output
        shape:
            {"p10": D, "p50": D, "p90": D, "mean": D, "stdev": D}
        We use stdev as fair_prob_stderr (for binary ML simulations it's the
        Bernoulli sample stdev, which is exactly what the uncertainty-factor
        formula expects). If the MC result lacks stdev, fall back to
        (p90 - p10) / 2.56 as a rough normal-equivalent.
        """
        stdev_raw = mc_result.get("stdev")
        if stdev_raw is not None:
            stdev = Decimal(str(stdev_raw))
        else:
            p10 = mc_result.get("p10")
            p90 = mc_result.get("p90")
            if p10 is None or p90 is None:
                stdev = Decimal('0')
            else:
                stdev = (Decimal(str(p90)) - Decimal(str(p10))) / Decimal('2.56')
                if stdev < Decimal('0'):
                    stdev = Decimal('0')
        inputs = KellyInputs(
            edge=edge if isinstance(edge, Decimal) else Decimal(str(edge)),
            decimal_odds=decimal_odds if isinstance(decimal_odds, Decimal) else Decimal(str(decimal_odds)),
            fair_prob_stderr=stdev,
            sample_size=sample_size,
            portfolio_size=portfolio_size,
            max_sibling_corr=max_sibling_corr,
        )
        return AdaptiveKelly.compute(inputs)

    @staticmethod
    def compute(inputs: KellyInputs) -> KellyResult:
        if inputs.edge < EDGE_FLOOR:
            zero = Decimal('0').quantize(Decimal('0.000001'))
            return KellyResult(
                full_kelly=zero,
                uncertainty_factor=zero,
                sample_factor=zero,
                portfolio_factor=zero,
                correlation_factor=zero,
                base_fraction=BASE_FRACTION,
                pre_cap=zero,
                kelly_final=zero,
                capped=False,
            )

        full = AdaptiveKelly._full_kelly(inputs.edge, inputs.decimal_odds)
        unc = AdaptiveKelly._uncertainty_factor(inputs.edge, inputs.fair_prob_stderr)
        samp = AdaptiveKelly._sample_factor(inputs.sample_size)
        port = AdaptiveKelly._portfolio_factor(inputs.portfolio_size)
        corr = AdaptiveKelly._correlation_factor(inputs.max_sibling_corr)

        pre_cap = (BASE_FRACTION * full * unc * samp * port * corr).quantize(Decimal('0.000001'))

        if pre_cap > PER_BET_CAP:
            final = PER_BET_CAP.quantize(Decimal('0.000001'))
            capped = True
        else:
            final = pre_cap
            capped = False

        return KellyResult(
            full_kelly=full,
            uncertainty_factor=unc,
            sample_factor=samp,
            portfolio_factor=port,
            correlation_factor=corr,
            base_fraction=BASE_FRACTION,
            pre_cap=pre_cap,
            kelly_final=final,
            capped=capped,
        )

    @staticmethod
    def apply_daily_cap(candidate: Decimal, running_total: Decimal) -> Decimal:
        """
        Trim a candidate allocation so that running_total + candidate <= DAILY_CAP.
        Returns the (possibly reduced) candidate; never negative.
        """
        if running_total >= DAILY_CAP:
            return Decimal('0').quantize(Decimal('0.000001'))
        remaining = DAILY_CAP - running_total
        if candidate > remaining:
            return remaining.quantize(Decimal('0.000001'))
        return candidate.quantize(Decimal('0.000001'))
