"""Probability calibration: isotonic regression or Platt (logistic) scaling.

Train on a held-out slice of recent games (the calibrator should *not*
see the same rows as the underlying classifier — that's why the
training pipeline reserves `model.calibration_holdout_frac`).
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

import numpy as np


@dataclass
class Calibrator:
    method: Literal["isotonic", "platt"] = "isotonic"
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
