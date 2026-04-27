"""Calibration utilities — consolidation module.

Re-exports the existing calibrators and adds the rolling-holdout
variant + a printable reliability summary the audit asked for.

The two existing calibrators
----------------------------
* `Calibrator` (from `nrfi.models.calibration`) — sklearn-backed
  isotonic / Platt. Fast, used by the daily inference path.
* `CoreIsotonicCalibrator` (from `nrfi.integration.calibration`) —
  Decimal-precision PAV from `edge_equation.math.isotonic`. Slightly
  slower, used when downstream needs the deterministic core's exact
  primitive.

New in phase 3
--------------
* `RollingHoldoutCalibrator` — refits isotonic on the trailing N days
  of completed games every time a new day is processed in a backtest.
  This is the "as-known pre-game" calibrator: never fits on data the
  model wouldn't have seen.
* `reliability_summary(probs, y, n_bins=10)` — returns a list of
  human-readable bin lines like `"70-80%  pred 74.2%  actual 72.1%
  n=183"` and prints them when called from the CLI.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

from ..integration.calibration import CoreIsotonicCalibrator
from ..models.calibration import Calibrator

__all__ = [
    "Calibrator",
    "CoreIsotonicCalibrator",
    "RollingHoldoutCalibrator",
    "reliability_summary",
    "BinSummary",
]


# ---------------------------------------------------------------------------
# Reliability summary helper
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BinSummary:
    lo: float          # bin lower bound (0..1)
    hi: float
    pred_mean: float   # mean predicted probability in bin
    actual: float      # empirical hit rate
    n: int

    def line(self) -> str:
        if self.n == 0:
            return f"  [{self.lo*100:5.1f}%-{self.hi*100:5.1f}%]  empty"
        return (f"  [{self.lo*100:5.1f}%-{self.hi*100:5.1f}%]  "
                f"pred {self.pred_mean*100:5.1f}%  "
                f"actual {self.actual*100:5.1f}%  n={self.n}")


def reliability_summary(
    probs: Sequence[float], y_true: Sequence[int],
    *, n_bins: int = 10, brier: Optional[float] = None,
    print_to_stdout: bool = False,
) -> list[BinSummary]:
    """Compute per-bin hit rates plus an optional pretty-print.

    Returns a list of `BinSummary` even when bins are empty so callers
    can render the full ladder.
    """
    p = np.asarray(probs, dtype=float)
    y = np.asarray(y_true, dtype=int)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: list[BinSummary] = []
    for i in range(n_bins):
        lo, hi = float(edges[i]), float(edges[i + 1])
        if i == n_bins - 1:
            mask = (p >= lo) & (p <= hi)
        else:
            mask = (p >= lo) & (p < hi)
        if mask.any():
            out.append(BinSummary(
                lo=lo, hi=hi,
                pred_mean=float(p[mask].mean()),
                actual=float(y[mask].mean()),
                n=int(mask.sum()),
            ))
        else:
            out.append(BinSummary(lo=lo, hi=hi, pred_mean=(lo+hi)/2,
                                    actual=float("nan"), n=0))
    if print_to_stdout:
        print("\n--- Reliability bins ---")
        for b in out:
            print(b.line())
        if brier is not None:
            print(f"  Brier: {brier:.4f}")
    return out


# ---------------------------------------------------------------------------
# Rolling-holdout calibrator
# ---------------------------------------------------------------------------

class RollingHoldoutCalibrator:
    """Rolling-window isotonic calibrator for backtest replay.

    Usage in a date-by-date backtest loop::

        cal = RollingHoldoutCalibrator(window_size=400)
        for d in dates:
            preds = engine.predict(features_for(d))
            calibrated = [cal.transform(p) for p in preds]
            # ... later, after results land ...
            cal.add_observations(preds, actuals_for(d))
            cal.refit()    # cheap — isotonic is O(n log n)

    Maintains a sliding window of the last `window_size` (raw_prob, y)
    pairs. Refit cost grows with window — 400-1000 is the sweet spot.

    This is *backtest-safe*: a probability emitted on day D is
    transformed using only observations from days <= D-1.
    """

    def __init__(self, window_size: int = 400, method: str = "isotonic"):
        self._window: deque[tuple[float, int]] = deque(maxlen=window_size)
        self._method = method
        self._calibrator: Optional[Calibrator] = None

    @property
    def fitted(self) -> bool:
        return self._calibrator is not None

    def add_observations(self, raw_probs: Iterable[float],
                          y_true: Iterable[int]) -> None:
        for p, y in zip(raw_probs, y_true):
            self._window.append((float(p), int(y)))

    def refit(self, *, min_samples: int = 50) -> bool:
        """Refit the calibrator from the current window. Returns True on
        success, False if the window is too thin."""
        if len(self._window) < min_samples:
            return False
        ps = [p for p, _ in self._window]
        ys = [y for _, y in self._window]
        self._calibrator = Calibrator(method=self._method).fit(ps, ys)
        return True

    def transform(self, raw_prob: float) -> float:
        """Return the calibrated probability. Falls back to the raw
        value when the window hasn't seeded yet."""
        if self._calibrator is None:
            return float(raw_prob)
        return float(self._calibrator.transform([float(raw_prob)])[0])

    def transform_batch(self, raw_probs: Iterable[float]) -> list[float]:
        if self._calibrator is None:
            return [float(p) for p in raw_probs]
        return list(self._calibrator.transform(list(raw_probs)))
