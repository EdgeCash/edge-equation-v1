"""Probability calibration: isotonic regression, Platt (logistic) scaling,
or beta calibration.

Train on a held-out slice of recent games (the calibrator should *not*
see the same rows as the underlying classifier — that's why the
training pipeline reserves `model.calibration_holdout_frac`).

Method choice:
* ``isotonic`` — non-parametric monotone fit. Sharp on small bins;
  collapses output dispersion to ~1% on noisy NRFI samples (this was
  the bottleneck the calibration audit revealed).
* ``platt`` — single-feature logistic regression on raw scores. Stable
  but underfits local reliability.
* ``beta`` — three-parameter Kull/Filho/Flach 2017 beta calibration.
  Logistic regression on (log p, log 1-p) gives a strict generalization
  of Platt with much better small-sample behavior on noisy sports
  probabilities. **Production default since 2026-05-01** after the
  audit confirmed beta wins on Brier, log-loss, and ECE while preserving
  ≥64% high-conviction picks.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

import numpy as np


@dataclass
class Calibrator:
    method: Literal["isotonic", "platt", "beta"] = "isotonic"
    _model: object | None = None

    # ---- Fit -----------------------------------------------------------
    def fit(self, raw_probs: Sequence[float], y_true: Sequence[int]) -> "Calibrator":
        raw = np.asarray(raw_probs, dtype=float)
        y = np.asarray(y_true, dtype=int)
        if raw.size == 0:
            raise ValueError("Calibrator needs at least one sample")

        if self.method == "isotonic":
            from sklearn.isotonic import IsotonicRegression  # type: ignore
            ir = IsotonicRegression(y_min=0.0, y_max=1.0,
                                    out_of_bounds="clip")
            ir.fit(raw, y)
            self._model = ir
        elif self.method == "platt":
            from sklearn.linear_model import LogisticRegression  # type: ignore
            lr = LogisticRegression(C=1.0, solver="lbfgs", max_iter=200)
            lr.fit(raw.reshape(-1, 1), y)
            self._model = lr
        elif self.method == "beta":
            # Kull/Filho/Flach 2017 — three-parameter beta calibration.
            # Map raw p → sigmoid(a·log p − b·log(1−p) + c). Strict
            # generalization of Platt (which is a == b). Stored as a
            # plain dict so save/load round-trips through pickle without
            # needing the sklearn model object.
            from sklearn.linear_model import LogisticRegression  # type: ignore
            eps = 1e-6
            clipped = np.clip(raw, eps, 1.0 - eps)
            X = np.column_stack([np.log(clipped), -np.log(1.0 - clipped)])
            lr = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500)
            lr.fit(X, y)
            self._model = {
                "a": float(lr.coef_[0][0]),
                "b": float(lr.coef_[0][1]),
                "c": float(lr.intercept_[0]),
                "eps": eps,
            }
        else:
            raise ValueError(f"Unknown calibration method: {self.method}")
        return self

    # ---- Predict -------------------------------------------------------
    def transform(self, raw_probs: Sequence[float]) -> np.ndarray:
        if self._model is None:
            return np.asarray(raw_probs, dtype=float)
        raw = np.asarray(raw_probs, dtype=float).reshape(-1)
        if self.method == "isotonic":
            return self._model.transform(raw)
        if self.method == "beta":
            m = self._model
            eps = m["eps"]
            clipped = np.clip(raw, eps, 1.0 - eps)
            z = m["a"] * np.log(clipped) - m["b"] * np.log(1.0 - clipped) + m["c"]
            return 1.0 / (1.0 + np.exp(-z))
        return self._model.predict_proba(raw.reshape(-1, 1))[:, 1]

    # ---- Persistence ---------------------------------------------------
    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump({"method": self.method, "model": self._model}, fh)

    @classmethod
    def load(cls, path: str | Path) -> "Calibrator":
        with Path(path).open("rb") as fh:
            blob = pickle.load(fh)
        c = cls(method=blob["method"])
        c._model = blob["model"]
        return c
