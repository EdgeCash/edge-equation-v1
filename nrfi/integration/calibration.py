"""Decimal-aware calibration adapter.

The NRFI engine's `models/calibration.py` uses sklearn's `IsotonicRegression`
internally (float). The deterministic Edge Equation core ships its own
PAV-based `IsotonicRegressor` that operates in Decimal precision and is
the canonical calibrator for downstream Pick.fair_prob values.

This adapter fits the core's regressor on a NRFI holdout and exposes a
float-in/float-out API so the rest of the NRFI engine doesn't need to
care about Decimal plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass
class CoreIsotonicCalibrator:
    """Wraps `edge_equation.math.isotonic.IsotonicRegressor` so the NRFI
    engine can use the same calibration primitive the rest of the
    deterministic stack uses. Lazy-imports the core math layer."""

    _fit: object | None = None  # IsotonicFit (Decimal-typed)

    def fit(self, raw_probs: Sequence[float], y_true: Sequence[int]) -> "CoreIsotonicCalibrator":
        from edge_equation.math.isotonic import IsotonicRegressor
        x = [Decimal(str(float(p))) for p in raw_probs]
        y = [Decimal(str(int(yi))) for yi in y_true]
        self._fit = IsotonicRegressor.fit(x, y)
        return self

    def transform(self, raw_probs: Sequence[float]) -> list[float]:
        from edge_equation.math.isotonic import IsotonicRegressor
        if self._fit is None:
            return [float(p) for p in raw_probs]
        return [float(IsotonicRegressor.predict(self._fit, Decimal(str(float(p)))))
                for p in raw_probs]

    def transform_one(self, raw_prob: float) -> float:
        return self.transform([raw_prob])[0]
