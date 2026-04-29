"""Training pipelines for the elite NRFI/YRFI ML stack.

Two complementary models:

* `NRFIClassifier` — XGBoost binary classifier on the calibrated NRFI
  target. Output blended with the closed-form Poisson baseline by
  `inference.NRFIInferenceEngine`.

* `FirstInningRunsRegressor` — XGBoost Poisson regressor on actual
  first-inning runs. λ is converted via `exp(-λ)` for the secondary
  NRFI estimate; useful for ensembling and as a sanity check on the
  classifier head.

A LightGBM mirror of the classifier is also fit when LightGBM is
available — modest ROC-AUC bumps from ensembling typically show up.

Training inputs:
    df : pd.DataFrame with at minimum a `feature_blob` JSON column,
         a `nrfi` boolean column, and a `first_inn_runs` int column,
         as produced by `NRFIStore.training_frame()`.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import NRFIConfig, get_default_config
from edge_equation.utils.logging import get_logger
from .calibration import Calibrator
from .poisson_baseline import PoissonGLM

log = get_logger(__name__)

MODEL_VERSION = "elite_nrfi_v1"


# --- Feature matrix prep -------------------------------------------------

def expand_feature_blobs(df: pd.DataFrame) -> pd.DataFrame:
    """Explode the `feature_blob` JSON column into a wide DataFrame."""
    blobs = df["feature_blob"].apply(json.loads).tolist()
    feat = pd.DataFrame(blobs).fillna(0.0)
    out = pd.concat([df.drop(columns=["feature_blob"]).reset_index(drop=True),
                     feat.reset_index(drop=True)], axis=1)
    return out


def feature_matrix(df: pd.DataFrame, *, drop_cols: Sequence[str] = ()) -> tuple[pd.DataFrame, list[str]]:
    """Return the X DataFrame and column list ready for XGBoost / LightGBM.

    Drops:
      * Identifier / target columns (game_pk, nrfi, first_inn_runs, …)
      * `game_date` — metadata only; XGBoost rejects datetime64 dtypes.
      * Any other datetime / timedelta column that sneaks in via a
        future schema addition (defensive).
      * Anything in the caller-supplied `drop_cols`.
    """
    drop = set([
        "game_pk", "model_version", "nrfi", "first_inn_runs",
        "game_date",         # datetime64 — XGBoost rejects it as a feature
    ]) | set(drop_cols)
    # Defensively drop any column whose dtype is datetime / timedelta.
    # Catches future schema additions before they trip the trainer.
    for c in df.columns:
        try:
            if pd.api.types.is_datetime64_any_dtype(df[c]) or \
               pd.api.types.is_timedelta64_dtype(df[c]):
                drop.add(c)
        except Exception:
            pass
    cols = [c for c in df.columns if c not in drop]
    return df[cols].copy(), cols


# --- Estimators ----------------------------------------------------------

@dataclass
class NRFIClassifier:
    """XGBoost binary classifier head + isotonic calibrator."""

    feature_names: list[str] = field(default_factory=list)
    _booster: object | None = None
    _lgbm: object | None = None
    _calibrator: Calibrator | None = None
    blend_with_lgbm: bool = True

    # ---- Fit -----------------------------------------------------------
    def fit(self, X: pd.DataFrame, y: Sequence[int],
            *, params: Mapping | None = None,
            calibration_holdout_frac: float = 0.2,
            calibration_method: str = "isotonic",
            lgbm_params: Mapping | None = None) -> "NRFIClassifier":
        from xgboost import XGBClassifier  # type: ignore
        self.feature_names = list(X.columns)

        # Hold out the most recent N% for calibration so the calibrator
        # never sees rows the booster trained on.
        n = len(X)
        cut = int(n * (1 - calibration_holdout_frac))
        X_train, X_cal = X.iloc[:cut], X.iloc[cut:]
        y_arr = np.asarray(y, dtype=int)
        y_train, y_cal = y_arr[:cut], y_arr[cut:]

        booster = XGBClassifier(**(params or {}))
        booster.fit(X_train, y_train, eval_set=[(X_cal, y_cal)], verbose=False)
        self._booster = booster

        if self.blend_with_lgbm:
            try:
                from lightgbm import LGBMClassifier  # type: ignore
                lgbm = LGBMClassifier(**(lgbm_params or {}))
                lgbm.fit(X_train, y_train)
                self._lgbm = lgbm
            except ImportError:
                log.info("LightGBM not installed — skipping ensemble.")

        # Fit calibrator on the holdout slice.
        raw_cal = self._raw_predict(X_cal)
        self._calibrator = Calibrator(method=calibration_method).fit(raw_cal, y_cal)
        return self

    # ---- Predict -------------------------------------------------------
    def _raw_predict(self, X: pd.DataFrame) -> np.ndarray:
        x = X[self.feature_names]
        p_xgb = self._booster.predict_proba(x)[:, 1]
        if self._lgbm is None:
            return p_xgb
        p_lgb = self._lgbm.predict_proba(x)[:, 1]
        return 0.5 * (p_xgb + p_lgb)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        raw = self._raw_predict(X)
        if self._calibrator is None:
            return raw
        return self._calibrator.transform(raw)

    # ---- Persistence ---------------------------------------------------
    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(
                {
                    "feature_names": self.feature_names,
                    "booster": self._booster,
                    "lgbm": self._lgbm,
                    "calibrator": self._calibrator,
                    "version": MODEL_VERSION,
                },
                fh,
            )

    @classmethod
    def load(cls, path: str | Path) -> "NRFIClassifier":
        with Path(path).open("rb") as fh:
            blob = pickle.load(fh)
        obj = cls(feature_names=blob["feature_names"])
        obj._booster = blob["booster"]
        obj._lgbm = blob.get("lgbm")
        obj._calibrator = blob.get("calibrator")
        return obj


@dataclass
class FirstInningRunsRegressor:
    """XGBoost Poisson regressor on actual first-inning runs."""

    feature_names: list[str] = field(default_factory=list)
    _booster: object | None = None

    def fit(self, X: pd.DataFrame, y: Sequence[float],
            *, params: Mapping | None = None) -> "FirstInningRunsRegressor":
        from xgboost import XGBRegressor  # type: ignore
        self.feature_names = list(X.columns)
        booster = XGBRegressor(**(params or {}))
        booster.fit(X, np.asarray(y, dtype=float))
        self._booster = booster
        return self

    def predict_lambda(self, X: pd.DataFrame) -> np.ndarray:
        return np.maximum(0.0, self._booster.predict(X[self.feature_names]))

    def predict_nrfi(self, X: pd.DataFrame) -> np.ndarray:
        return np.exp(-self.predict_lambda(X))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump({"feature_names": self.feature_names,
                          "booster": self._booster}, fh)

    @classmethod
    def load(cls, path: str | Path) -> "FirstInningRunsRegressor":
        with Path(path).open("rb") as fh:
            blob = pickle.load(fh)
        obj = cls(feature_names=blob["feature_names"])
        obj._booster = blob["booster"]
        return obj


# --- High-level training entry -------------------------------------------

@dataclass
class TrainedBundle:
    classifier: NRFIClassifier
    regressor: FirstInningRunsRegressor
    poisson_glm: PoissonGLM
    feature_names: list[str]
    model_version: str = MODEL_VERSION

    def save(self, model_dir: str | Path) -> None:
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        self.classifier.save(model_dir / f"{self.model_version}_classifier.pkl")
        self.regressor.save(model_dir / f"{self.model_version}_regressor.pkl")
        with (model_dir / f"{self.model_version}_glm.pkl").open("wb") as fh:
            pickle.dump(self.poisson_glm, fh)
        (model_dir / f"{self.model_version}_features.json").write_text(
            json.dumps(self.feature_names)
        )
        (model_dir / f"{self.model_version}_metadata.json").write_text(
            json.dumps({
                "model_version": self.model_version,
                "feature_count": len(self.feature_names),
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2)
        )

    @classmethod
    def load(cls, model_dir: str | Path,
             version: str = MODEL_VERSION) -> "TrainedBundle":
        model_dir = Path(model_dir)
        classifier = NRFIClassifier.load(model_dir / f"{version}_classifier.pkl")
        regressor = FirstInningRunsRegressor.load(
            model_dir / f"{version}_regressor.pkl"
        )
        with (model_dir / f"{version}_glm.pkl").open("rb") as fh:
            glm = pickle.load(fh)
        feats = json.loads((model_dir / f"{version}_features.json").read_text())
        return cls(classifier=classifier, regressor=regressor,
                   poisson_glm=glm, feature_names=feats, model_version=version)


def train_from_store(store, start_date: str, end_date: str,
                     *, config: NRFIConfig | None = None) -> TrainedBundle:
    """Train both heads using rows in [start_date, end_date]."""
    cfg = config or get_default_config()
    df = store.training_frame(start_date, end_date)
    if df is None or df.empty:
        raise RuntimeError("No training rows in window")
    log.info("Training rows: %d (%s..%s)", len(df), start_date, end_date)

    wide = expand_feature_blobs(df)
    X, cols = feature_matrix(wide)
    y_clf = wide["nrfi"].astype(int).values
    y_reg = wide["first_inn_runs"].astype(float).values

    clf = NRFIClassifier().fit(
        X, y_clf,
        params=cfg.model.xgb_classifier_params,
        calibration_holdout_frac=cfg.model.calibration_holdout_frac,
        calibration_method=cfg.model.calibration_method,
        lgbm_params=cfg.model.lgbm_params,
    )
    reg = FirstInningRunsRegressor().fit(
        X, y_reg, params=cfg.model.xgb_poisson_params,
    )
    glm = PoissonGLM().fit(X.values, y_reg, feature_names=cols)

    bundle = TrainedBundle(classifier=clf, regressor=reg, poisson_glm=glm,
                            feature_names=cols)
    bundle.save(cfg.model_dir)
    log.info("Saved bundle to %s", cfg.model_dir)
    return bundle
