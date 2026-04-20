"""
Monte Carlo simulator.

Deterministic: given a fixed seed and inputs, outputs are identical
across runs. Uses stdlib random.Random seeded at construction.

simulate_binary(prob): draws Bernoulli trials and returns quantiles of
the running mean. Useful for ML-type fair probabilities.

simulate_total(mean, stdev): draws from a normal distribution clipped
at zero and returns p10/p50/p90/mean.
"""
from decimal import Decimal, ROUND_HALF_UP
import random


def _quantile(sorted_values: list, q: float) -> float:
    """Linear-interpolation quantile on a pre-sorted list."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    # index = q * (n - 1)
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _to_decimal(x: float, places: str = "0.000001") -> Decimal:
    return Decimal(str(x)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


class MonteCarloSimulator:

    def __init__(self, seed: int = 42, iterations: int = 10000):
        if iterations <= 0:
            raise ValueError("iterations must be positive")
        self.seed = seed
        self.iterations = iterations

    def _rng(self) -> random.Random:
        """Fresh Random seeded for each simulate_* call — ensures determinism per call."""
        return random.Random(self.seed)

    def simulate_binary(self, prob) -> dict:
        """
        Simulate Bernoulli outcomes at the given probability and return
        quantiles of the running mean across iterations.

        Args:
            prob: fair probability (Decimal or float-compatible), 0 <= p <= 1
        Returns:
            dict with 'p10', 'p50', 'p90', 'mean' as Decimals (6 decimal places).
        """
        p = float(Decimal(str(prob)))
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"prob must be in [0, 1], got {p}")
        rng = self._rng()
        running = []
        hits = 0
        for i in range(1, self.iterations + 1):
            if rng.random() < p:
                hits += 1
            running.append(hits / i)
        sorted_running = sorted(running)
        mean_val = hits / self.iterations
        return {
            "p10": _to_decimal(_quantile(sorted_running, 0.10)),
            "p50": _to_decimal(_quantile(sorted_running, 0.50)),
            "p90": _to_decimal(_quantile(sorted_running, 0.90)),
            "mean": _to_decimal(mean_val),
        }

    def simulate_total(self, mean, stdev) -> dict:
        """
        Simulate totals from a normal(mean, stdev) clipped at zero.

        Args:
            mean:  Decimal or float-compatible
            stdev: Decimal or float-compatible (>= 0)
        Returns:
            dict with 'p10', 'p50', 'p90', 'mean' as Decimals (2 decimal places).
        """
        m = float(Decimal(str(mean)))
        s = float(Decimal(str(stdev)))
        if s < 0:
            raise ValueError(f"stdev must be non-negative, got {s}")
        rng = self._rng()
        samples = []
        for _ in range(self.iterations):
            x = rng.gauss(m, s)
            if x < 0:
                x = 0.0
            samples.append(x)
        sorted_samples = sorted(samples)
        mean_val = sum(samples) / len(samples)
        # Totals are rounded to 2 decimals to match rest of engine
        return {
            "p10": _to_decimal(_quantile(sorted_samples, 0.10), places="0.01"),
            "p50": _to_decimal(_quantile(sorted_samples, 0.50), places="0.01"),
            "p90": _to_decimal(_quantile(sorted_samples, 0.90), places="0.01"),
            "mean": _to_decimal(mean_val, places="0.01"),
        }
