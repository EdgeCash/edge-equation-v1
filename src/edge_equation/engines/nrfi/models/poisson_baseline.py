"""Closed-form Poisson conversion + GLM-style baseline.

P(NRFI) = P(0 runs in top 1st) × P(0 runs in bot 1st) = exp(-λ_total)

The baseline expects λ_top + λ_bot from the feature pipeline. A small
GLM (`fit_poisson_glm`) is provided so we can also *learn* a baseline λ
purely from features when the deterministic constants drift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


def poisson_p_no_runs(lam: float) -> float:
    """P(0 runs) given a Poisson-distributed run rate."""
    return math.exp(-max(0.0, lam))


def nrfi_from_lambdas(lam_top: float, lam_bot: float) -> float:
    return poisson_p_no_runs(lam_top) * poisson_p_no_runs(lam_bot)


def total_runs_distribution(lam: float, max_runs: int = 6) -> list[float]:
    """PMF over [0..max_runs] for total first-inning runs."""
    pmf = []
    for k in range(max_runs + 1):
        pmf.append(math.exp(-lam) * (lam ** k) / math.factorial(k))
    return pmf


@dataclass
class PoissonGLM:
    """Lightweight Poisson regressor (Newton-IRLS).

    Uses statsmodels if available (preferred for SE), falls back to
    sklearn's PoissonRegressor, and finally to a tiny pure-numpy
    IRLS so the package degrades gracefully.
    """

    coef_: np.ndarray | None = None
    intercept_: float = 0.0
    feature_names_: list[str] | None = None

    def fit(self, X, y, feature_names: Sequence[str] | None = None) -> "PoissonGLM":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        try:
            from sklearn.linear_model import PoissonRegressor  # type: ignore
            mdl = PoissonRegressor(alpha=0.5, max_iter=300)
            mdl.fit(X, y)
            self.coef_ = mdl.coef_
            self.intercept_ = mdl.intercept_
        except Exception:
            self.coef_, self.intercept_ = _irls(X, y, max_iter=50)
        self.feature_names_ = list(feature_names) if feature_names else None
        return self

    def predict_lambda(self, X) -> np.ndarray:
        if self.coef_ is None:
            raise RuntimeError("Model not fit yet")
        X = np.asarray(X, dtype=float)
        eta = X @ self.coef_ + self.intercept_
        return np.exp(np.clip(eta, -10.0, 10.0))

    def predict_nrfi(self, X) -> np.ndarray:
        return np.exp(-self.predict_lambda(X))


def _irls(X: np.ndarray, y: np.ndarray, max_iter: int = 50,
          tol: float = 1e-6) -> tuple[np.ndarray, float]:
    """Hand-rolled IRLS for Poisson log-link (fallback only)."""
    n, p = X.shape
    X1 = np.hstack([np.ones((n, 1)), X])
    beta = np.zeros(p + 1)
    for _ in range(max_iter):
        eta = np.clip(X1 @ beta, -10.0, 10.0)
        mu = np.exp(eta)
        z = eta + (y - mu) / np.maximum(mu, 1e-6)
        W = np.diag(mu)
        try:
            beta_new = np.linalg.solve(X1.T @ W @ X1 + 1e-3 * np.eye(p + 1),
                                        X1.T @ W @ z)
        except np.linalg.LinAlgError:
            break
        if np.max(np.abs(beta_new - beta)) < tol:
            beta = beta_new
            break
        beta = beta_new
    return beta[1:], float(beta[0])
