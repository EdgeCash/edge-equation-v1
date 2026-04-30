"""Alternative calibrators for NRFI probability output.

The default `Calibrator` (isotonic / Platt) was empirically observed to
crush NRFI probabilities into a narrow ~48-52% band on real holdout
slates — see PR #85's diagnostic. The author's own conclusion was that
the obvious blending tweaks did not move the needle.

This module ships *alternatives* — beta calibration, sample-aware
smoothed isotonic, and a confidence-aware temperature scaler — without
modifying the production `Calibrator`. The companion
`calibration_audit.py` runs the existing trained bundle's holdout
predictions through each alternative side-by-side so the operator can
empirically pick a winner (or, more likely, prove that the bottleneck
is feature signal rather than calibration).

All calibrators implement the minimal interface used by `Calibrator`::

    fit(raw_probs, y_true) -> self
    transform(raw_probs)   -> np.ndarray of calibrated probs

Nothing here is wired into the live pipeline. The audit is a read-only
diagnostic — operators decide what to promote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Beta calibration (Kull, Filho, Flach 2017)
# ---------------------------------------------------------------------------


@dataclass
class BetaCalibrator:
    """Three-parameter beta calibration.

    Maps raw probability ``p`` to::

        sigmoid(a * log(p) - b * log(1-p) + c)

    Equivalent to fitting a logistic regression on the two log-features
    ``log(p)`` and ``log(1-p)``. Strictly more flexible than Platt
    (which is the special case ``a == b``) and far less aggressive than
    isotonic on small samples. Recommended baseline for noisy sports
    probabilities per Kull et al.

    Reference: "Beta calibration: a well-founded and easily implemented
    improvement on logistic calibration for binary classifiers", Kull,
    Filho, Flach (AISTATS 2017).
    """

    eps: float = 1e-6
    _a: float = 0.0
    _b: float = 0.0
    _c: float = 0.0
    _fitted: bool = False

    def fit(self, raw_probs: Sequence[float], y_true: Sequence[int]) -> "BetaCalibrator":
        from sklearn.linear_model import LogisticRegression  # type: ignore

        raw = np.asarray(raw_probs, dtype=float).reshape(-1)
        y = np.asarray(y_true, dtype=int).reshape(-1)
        if raw.size == 0:
            raise ValueError("BetaCalibrator needs at least one sample")
        clipped = np.clip(raw, self.eps, 1.0 - self.eps)
        # Two features per row: log(p), log(1-p). LR gives (a, -b, c).
        X = np.column_stack([np.log(clipped), -np.log(1.0 - clipped)])
        lr = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500)
        lr.fit(X, y)
        self._a = float(lr.coef_[0][0])
        self._b = float(lr.coef_[0][1])
        self._c = float(lr.intercept_[0])
        self._fitted = True
        return self

    def transform(self, raw_probs: Sequence[float]) -> np.ndarray:
        if not self._fitted:
            return np.asarray(raw_probs, dtype=float)
        raw = np.asarray(raw_probs, dtype=float).reshape(-1)
        clipped = np.clip(raw, self.eps, 1.0 - self.eps)
        z = self._a * np.log(clipped) - self._b * np.log(1.0 - clipped) + self._c
        return _sigmoid(z)


# ---------------------------------------------------------------------------
# Sample-aware smoothed isotonic
# ---------------------------------------------------------------------------


@dataclass
class SmoothedIsotonicCalibrator:
    """Isotonic regression with a minimum-samples-per-bin constraint.

    Vanilla `IsotonicRegression` collapses neighboring samples into
    arbitrarily small bins, which is exactly the failure mode observed
    on the NRFI corpus (2,430 holdout points → output std 1.1%).

    This calibrator first bins the raw scores into approximately
    ``min_samples_per_bin``-sized buckets (deciles/percentiles), takes
    the empirical hit-rate per bin, then linearly interpolates the
    monotone-rectified bin centers. The result preserves any genuine
    monotone signal while refusing to commit to a fine-grained
    reliability curve the sample doesn't support.

    Tuning knob:
    * ``min_samples_per_bin``: floor on bin size. Higher = smoother.
      Default 50 is a sane starting point for ~2-3k holdout samples.
    """

    min_samples_per_bin: int = 50
    eps: float = 1e-6
    _bin_edges: np.ndarray = field(default_factory=lambda: np.array([]))
    _bin_means: np.ndarray = field(default_factory=lambda: np.array([]))
    _fitted: bool = False

    def fit(self, raw_probs: Sequence[float], y_true: Sequence[int]) -> "SmoothedIsotonicCalibrator":
        raw = np.asarray(raw_probs, dtype=float).reshape(-1)
        y = np.asarray(y_true, dtype=int).reshape(-1)
        n = raw.size
        if n == 0:
            raise ValueError("SmoothedIsotonicCalibrator needs at least one sample")

        n_bins = max(2, int(np.floor(n / max(self.min_samples_per_bin, 1))))
        # Quantile bin edges so each bin holds ≈min_samples_per_bin points.
        quantiles = np.linspace(0.0, 1.0, n_bins + 1)
        edges = np.quantile(raw, quantiles)
        # Defensive: nudge any duplicate edges so digitize is well-defined.
        for i in range(1, len(edges)):
            if edges[i] <= edges[i - 1]:
                edges[i] = edges[i - 1] + self.eps
        idx = np.clip(np.digitize(raw, edges[1:-1], right=False), 0, n_bins - 1)
        bin_means = np.zeros(n_bins)
        for b in range(n_bins):
            mask = idx == b
            if mask.any():
                bin_means[b] = float(y[mask].mean())
            else:
                # Empty bin — interpolate later
                bin_means[b] = np.nan
        bin_means = _fill_nans_then_isotonize(bin_means)
        self._bin_edges = edges
        self._bin_means = bin_means
        self._fitted = True
        return self

    def transform(self, raw_probs: Sequence[float]) -> np.ndarray:
        if not self._fitted:
            return np.asarray(raw_probs, dtype=float)
        raw = np.asarray(raw_probs, dtype=float).reshape(-1)
        n_bins = len(self._bin_means)
        # Bin center for interpolation = midpoint of the bin's edge interval
        centers = 0.5 * (self._bin_edges[:-1] + self._bin_edges[1:])
        # Linear interpolation between bin centers (clipped at endpoints)
        return np.interp(raw, centers, self._bin_means)


# ---------------------------------------------------------------------------
# Confidence-aware temperature scaling
# ---------------------------------------------------------------------------


@dataclass
class ConfidenceTemperatureCalibrator:
    """Temperature scaling with a confidence-dependent temperature.

    Standard temperature scaling fits one scalar T such that the
    sharpened/softened probability ``sigmoid(logit(p) / T)`` minimizes
    log-loss. This variant fits *two* temperatures: ``T_high`` for
    predictions far from 0.5 and ``T_low`` for predictions near 0.5,
    blended by the raw signal strength ``2 * |p - 0.5|``::

        T(p) = T_low + signal_strength * (T_high - T_low)
        calibrated = sigmoid(logit(p) / T(p))

    When ``T_high < 1``, high-confidence raw predictions are *sharpened*
    further; when > 1, they're softened. This is the closest formal
    expression of the user's stated "controlled raw-signal residual
    blending: give more weight to raw XGBoost when feature signal is
    strong" — but as a calibration knob, fit empirically.

    Two scalars only — much harder to overfit than isotonic on a
    2-3k holdout.
    """

    eps: float = 1e-6
    _t_low: float = 1.0
    _t_high: float = 1.0
    _fitted: bool = False

    def fit(self, raw_probs: Sequence[float], y_true: Sequence[int]) -> "ConfidenceTemperatureCalibrator":
        raw = np.asarray(raw_probs, dtype=float).reshape(-1)
        y = np.asarray(y_true, dtype=int).reshape(-1)
        if raw.size == 0:
            raise ValueError("ConfidenceTemperatureCalibrator needs at least one sample")
        clipped = np.clip(raw, self.eps, 1.0 - self.eps)
        logits = np.log(clipped / (1.0 - clipped))
        signal = np.abs(raw - 0.5) * 2.0

        # Tiny grid search — two scalars in [0.25, 4.0]. log-loss is
        # smooth + convex per axis; this is faster + more transparent
        # than scipy.optimize for a 2D problem.
        grid = np.linspace(0.25, 4.0, 31)
        best = (np.inf, 1.0, 1.0)
        for tl in grid:
            for th in grid:
                t = tl + signal * (th - tl)
                p_cal = _sigmoid(logits / np.maximum(t, self.eps))
                loss = _log_loss(p_cal, y, eps=self.eps)
                if loss < best[0]:
                    best = (float(loss), float(tl), float(th))
        self._t_low = best[1]
        self._t_high = best[2]
        self._fitted = True
        return self

    def transform(self, raw_probs: Sequence[float]) -> np.ndarray:
        if not self._fitted:
            return np.asarray(raw_probs, dtype=float)
        raw = np.asarray(raw_probs, dtype=float).reshape(-1)
        clipped = np.clip(raw, self.eps, 1.0 - self.eps)
        logits = np.log(clipped / (1.0 - clipped))
        signal = np.abs(raw - 0.5) * 2.0
        t = self._t_low + signal * (self._t_high - self._t_low)
        return _sigmoid(logits / np.maximum(t, self.eps))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def _log_loss(p: np.ndarray, y: np.ndarray, *, eps: float = 1e-6) -> float:
    p = np.clip(p, eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _fill_nans_then_isotonize(values: np.ndarray) -> np.ndarray:
    """Linearly interpolate NaN bins, then enforce monotonicity via PAV.

    We use a tiny in-place pool-adjacent-violators pass rather than
    sklearn's IsotonicRegression because the input here is already
    binned bin-means (≤50 points), and we want zero external state.
    """
    out = values.astype(float).copy()
    n = len(out)
    if n == 0:
        return out
    # Step 1: fill NaNs by linear interpolation against valid neighbors.
    valid = ~np.isnan(out)
    if not valid.any():
        return np.zeros_like(out)
    idx = np.arange(n)
    out = np.interp(idx, idx[valid], out[valid])
    # Step 2: PAV — sweep left→right, when out[i] < out[i-1] pool them
    # into the average and propagate backward.
    i = 1
    while i < n:
        if out[i] < out[i - 1]:
            j = i
            s = out[i]
            count = 1
            while j > 0 and out[j - 1] > s / count:
                j -= 1
                s += out[j]
                count += 1
            out[j:i + 1] = s / count
        i += 1
    return np.clip(out, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def build_calibrator(name: str, **kwargs):
    """Factory used by the audit to instantiate one of the alternatives
    (or the production `Calibrator`) by name."""
    name = name.strip().lower()
    if name == "beta":
        return BetaCalibrator(**kwargs)
    if name in ("smoothed_isotonic", "smooth_iso"):
        return SmoothedIsotonicCalibrator(**kwargs)
    if name in ("conf_temperature", "confidence_temperature"):
        return ConfidenceTemperatureCalibrator(**kwargs)
    if name in ("isotonic", "platt"):
        from .calibration import Calibrator
        return Calibrator(method=name)
    raise ValueError(f"Unknown calibrator: {name!r}")


ALTERNATIVE_NAMES: tuple[str, ...] = (
    "isotonic",                    # production baseline (reference point)
    "platt",                       # production baseline (reference point)
    "beta",
    "smoothed_isotonic",
    "conf_temperature",
)
