"""Inference orchestrator: blend ML + Poisson, attach SHAP drivers,
   render color/signal/Kelly stake.

Outputs a `Prediction` dataclass per game which is also persistable to
the DuckDB `predictions` table.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ..config import NRFIConfig, get_default_config
from ..utils.colors import gradient_hex, nrfi_band
from ..utils.kelly import StakeRecommendation, kelly_stake
from ..utils.logging import get_logger
from .model_training import MODEL_VERSION, TrainedBundle

log = get_logger(__name__)


@dataclass
class Prediction:
    game_pk: int
    nrfi_prob: float                # calibrated 0..1
    nrfi_pct: float                 # 0..100, rounded 1dp
    lambda_total: float
    color_band: str
    color_hex: str
    signal: str                     # STRONG_NRFI / LEAN_NRFI / COIN_FLIP / ...
    poisson_p_nrfi: float
    ml_p_nrfi: float
    blended_p_nrfi: float
    mc_low: Optional[float] = None
    mc_high: Optional[float] = None
    shap_drivers: list[tuple[str, float]] = field(default_factory=list)
    market_prob: Optional[float] = None
    edge: Optional[float] = None
    kelly_units: Optional[float] = None
    model_version: str = MODEL_VERSION

    def as_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["shap_drivers"] = json.dumps(d["shap_drivers"])
        return d


class NRFIInferenceEngine:
    """Pulls together ML + Poisson + SHAP + Kelly into a Prediction."""

    def __init__(self, bundle: TrainedBundle,
                 config: NRFIConfig | None = None):
        self.bundle = bundle
        self.cfg = config or get_default_config()
        self._explainer = None
        if self.cfg.enable_shap:
            try:
                import shap  # type: ignore
                self._explainer = shap.TreeExplainer(self.bundle.classifier._booster)
            except Exception as e:
                log.warning("SHAP disabled (%s)", e)
                self._explainer = None

    # ------------------------------------------------------------------
    def predict_one(
        self,
        feature_dict: Mapping[str, float],
        *,
        game_pk: int,
        market_prob: Optional[float] = None,
        american_odds: float = -110.0,
    ) -> Prediction:
        return self.predict_many(
            [feature_dict], game_pks=[game_pk],
            market_probs=[market_prob] if market_prob is not None else None,
            american_odds=[american_odds],
        )[0]

    def predict_many(
        self,
        feature_dicts: Sequence[Mapping[str, float]],
        *,
        game_pks: Sequence[int],
        market_probs: Optional[Sequence[Optional[float]]] = None,
        american_odds: Optional[Sequence[float]] = None,
    ) -> list[Prediction]:
        if not feature_dicts:
            return []

        # Align feature columns to the trained schema.
        df = pd.DataFrame(list(feature_dicts)).fillna(0.0)
        for c in self.bundle.feature_names:
            if c not in df.columns:
                df[c] = 0.0
        X = df[self.bundle.feature_names]

        ml_p = self.bundle.classifier.predict_proba(X)
        lam = self.bundle.regressor.predict_lambda(X)
        # Poisson baseline straight from features (already engineered).
        poisson_p = np.array([fd.get("poisson_p_nrfi",
                                     float(np.exp(-fd.get("lambda_total", 1.0))))
                              for fd in feature_dicts])
        # Blend ML head with Poisson conversion of the regression λ AND the
        # closed-form baseline. Equal weight between the two NRFI estimates
        # of λ, then the user-tunable convex blend with the ML head.
        lam_p = np.exp(-np.maximum(lam, 0.0))
        baseline_p = 0.5 * (lam_p + poisson_p)
        w = self.cfg.model.ml_blend_weight
        blended = w * ml_p + (1 - w) * baseline_p

        # SHAP top-N for each row (optional).
        shap_top: list[list[tuple[str, float]]] = [[] for _ in range(len(X))]
        if self._explainer is not None:
            try:
                vals = self._explainer.shap_values(X)
                # Newer SHAP returns the array directly for binary models.
                vals = np.asarray(vals)
                if vals.ndim == 3:  # legacy [class, samples, features]
                    vals = vals[1]
                for i in range(vals.shape[0]):
                    pairs = sorted(
                        zip(self.bundle.feature_names, vals[i]),
                        key=lambda kv: abs(kv[1]),
                        reverse=True,
                    )[:5]
                    shap_top[i] = [(name, float(v)) for name, v in pairs]
            except Exception as e:
                log.warning("SHAP shap_values failed: %s", e)

        market_probs = list(market_probs) if market_probs else [None] * len(X)
        american_odds = list(american_odds) if american_odds else [-110.0] * len(X)

        out: list[Prediction] = []
        for i, gpk in enumerate(game_pks):
            band = nrfi_band(blended[i] * 100.0)
            stake: Optional[StakeRecommendation] = None
            if market_probs[i] is not None:
                stake = kelly_stake(
                    model_prob=float(blended[i]),
                    market_prob=float(market_probs[i]),
                    american_odds=float(american_odds[i]),
                    fraction=self.cfg.betting.kelly_fraction,
                    min_edge=self.cfg.betting.min_edge_to_bet,
                    vig_buffer=self.cfg.betting.vig_buffer,
                    max_stake_units=self.cfg.betting.max_stake_units,
                )
            out.append(Prediction(
                game_pk=int(gpk),
                nrfi_prob=float(blended[i]),
                nrfi_pct=round(float(blended[i]) * 100.0, 1),
                lambda_total=float(lam[i]),
                color_band=band.label,
                color_hex=gradient_hex(blended[i] * 100.0),
                signal=band.signal,
                poisson_p_nrfi=float(poisson_p[i]),
                ml_p_nrfi=float(ml_p[i]),
                blended_p_nrfi=float(blended[i]),
                shap_drivers=shap_top[i],
                market_prob=market_probs[i],
                edge=stake.edge if stake else None,
                kelly_units=stake.stake_units if stake else None,
            ))
        return out

    def attach_monte_carlo(self, predictions: list[Prediction],
                           feature_dicts: Sequence[Mapping[str, float]]) -> None:
        """Refine predictions in-place with per-PA Monte Carlo bands."""
        if not self.cfg.enable_monte_carlo:
            return
        try:
            from ..simulation.monte_carlo import simulate_first_inning
        except ImportError:
            return
        for pred, fd in zip(predictions, feature_dicts):
            res = simulate_first_inning(fd, self.cfg.monte_carlo)
            pred.mc_low = res.low
            pred.mc_high = res.high
