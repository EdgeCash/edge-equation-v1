import json
from pathlib import Path
from typing import Optional, Dict, Any

from ..schema import Output, Market
from .deterministic import DeterministicWNBA
# v1's canonical isotonic class is IsotonicRegressor (returning an
# IsotonicFit). We provide a tiny IsotonicCalibrator-shaped wrapper so
# this bundle keeps the same API the rest of the pipeline expected.
from edge_equation.math.isotonic import IsotonicRegressor, IsotonicFit


class IsotonicCalibrator:
    """Compatibility wrapper -- predict() takes a float, returns a float.
    `from_dict` accepts the same shape `IsotonicFit.to_dict()` emits."""

    def __init__(self, fit: IsotonicFit):
        self._fit = fit

    @classmethod
    def from_dict(cls, data: dict) -> "IsotonicCalibrator":
        # IsotonicFit doesn't have a public `from_dict`, but its blocks
        # serialise to a list of (lo, hi, y) tuples we can re-fit from.
        # We rebuild an empty regressor seeded with the block boundaries
        # by feeding (mid_x, y) pairs back through fit().
        blocks = data.get("blocks") or []
        xs = [(b.get("lo", 0.0) + b.get("hi", 0.0)) / 2.0 for b in blocks]
        ys = [b.get("y", 0.0) for b in blocks]
        if not xs:
            # Empty calibrator -- identity transform.
            class _Identity:
                def predict_one(self, x):
                    return x
            return cls(_Identity())  # type: ignore[arg-type]
        return cls(IsotonicRegressor.fit(xs, ys, increasing=True))

    def transform(self, value: float) -> float:
        try:
            return float(IsotonicRegressor.predict(self._fit, value))
        except Exception:
            return value


class WNBAMLBundle:
    """
    ML wrapper for WNBA projections.
    - Loads XGBoost/LightGBM/sklearn models from disk
    - Applies isotonic calibration (optional)
    - Falls back to deterministic engine if model is missing
    """

    def __init__(
        self,
        deterministic_engine: DeterministicWNBA,
        model_dir: str = "models/wnba/",
        use_isotonic: bool = True,
    ):
        self.det = deterministic_engine
        self.model_dir = Path(model_dir)
        self.use_isotonic = use_isotonic

        self.models: Dict[str, Any] = {}
        self.calibrators: Dict[str, IsotonicCalibrator] = {}

        self._load_models()
        self._load_calibrators()

    # ---------------------------------------------------------
    # Model loading
    # ---------------------------------------------------------

    def _load_models(self):
        if not self.model_dir.exists():
            return

        for market in Market:
            model_path = self.model_dir / f"{market.value}_xgb.json"
            if model_path.exists():
                try:
                    import xgboost as xgb
                    booster = xgb.Booster()
                    booster.load_model(str(model_path))
                    self.models[market.value] = booster
                except Exception:
                    pass  # fail silently → fallback to deterministic

    def _load_calibrators(self):
        for market in Market:
            cal_path = self.model_dir / f"{market.value}_isotonic.json"
            if cal_path.exists():
                try:
                    with open(cal_path, "r") as f:
                        data = json.load(f)
                    self.calibrators[market.value] = IsotonicCalibrator.from_dict(data)
                except Exception:
                    pass

    # ---------------------------------------------------------
    # Prediction entry point
    # ---------------------------------------------------------

    def predict(
        self,
        market: Market,
        features: Dict[str, float],
        line: float,
        meta: Dict[str, Any],
    ) -> Output:
        """
        Main prediction method.
        - If ML model exists → use it
        - Else → deterministic fallback
        """

        model = self.models.get(market.value)

        if model is None:
            return self._predict_deterministic(market, features, line, meta)

        try:
            ml_pred = self._predict_ml(model, features)
            calibrated = self._apply_isotonic(market, ml_pred)
            return self._build_output_ml(market, calibrated, line, meta)
        except Exception:
            return self._predict_deterministic(market, features, line, meta)

    # ---------------------------------------------------------
    # ML prediction
    # ---------------------------------------------------------

    def _predict_ml(self, model, features: Dict[str, float]) -> float:
        import xgboost as xgb
        dmatrix = xgb.DMatrix([features])
        pred = float(model.predict(dmatrix)[0])
        return pred

    def _apply_isotonic(self, market: Market, value: float) -> float:
        if not self.use_isotonic:
            return value
        calibrator = self.calibrators.get(market.value)
        if calibrator is None:
            return value
        return calibrator.transform(value)

    # ---------------------------------------------------------
    # Deterministic fallback
    # ---------------------------------------------------------

    def _predict_deterministic(
        self,
        market: Market,
        features: Dict[str, float],
        line: float,
        meta: Dict[str, Any],
    ) -> Output:

        # Deterministic engine expects explicit inputs
        det = self.det

        if market == Market.POINTS:
            proj = det.project_points(
                usage=features["usage"],
                possessions=features["possessions"],
                shot_quality=features["shot_quality"],
            )
        elif market == Market.REBOUNDS:
            proj = det.project_rebounds(
                rebound_chance=features["rebound_chance"],
                minutes=features["minutes"],
            )
        elif market == Market.ASSISTS:
            proj = det.project_assists(
                assist_rate=features["assist_rate"],
                possessions=features["possessions"],
            )
        elif market == Market.PRA:
            pts = det.project_points(
                usage=features["usage"],
                possessions=features["possessions"],
                shot_quality=features["shot_quality"],
            )
            reb = det.project_rebounds(
                rebound_chance=features["rebound_chance"],
                minutes=features["minutes"],
            )
            ast = det.project_assists(
                assist_rate=features["assist_rate"],
                possessions=features["possessions"],
            )
            proj = pts + reb + ast
        elif market == Market.THREES:
            proj = det.project_threes(
                attempt_rate=features["three_rate"],
                minutes=features["minutes"],
                accuracy=features["three_accuracy"],
            )
        elif market == Market.FULLGAME_TOTAL:
            scores = det.project_fullgame_scores(
                team_ppp=features["team_ppp"],
                opp_ppp=features["opp_ppp"],
                possessions=features["possessions"],
            )
            proj = scores["team_score"] + scores["opp_score"]
        elif market == Market.FULLGAME_ML:
            scores = det.project_fullgame_scores(
                team_ppp=features["team_ppp"],
                opp_ppp=features["opp_ppp"],
                possessions=features["possessions"],
            )
            proj = det.project_fullgame_ml(scores["team_score"], scores["opp_score"])
        else:
            proj = 0.0

        # Probability comes from the deterministic engine's per-market
        # logistic-over-(proj-line). Grade + edge are computed inside
        # build_output so we don't double-count or drift from the new
        # canonical signature.
        probability = det.prob_over(market, proj, line)
        return det.build_output(
            market=market,
            player=meta.get("player"),
            team=meta.get("team"),
            opponent=meta.get("opponent"),
            projection=proj,
            line=line,
            probability=probability,
        )

    # ---------------------------------------------------------
    # ML output builder
    # ---------------------------------------------------------

    def _build_output_ml(
        self,
        market: Market,
        projection: float,
        line: float,
        meta: Dict[str, Any],
    ) -> Output:

        edge = projection - line
        probability = projection  # ML models output calibrated probability
        confidence = abs(edge)

        return Output(
            market=market,
            player=meta.get("player"),
            team=meta.get("team"),
            opponent=meta.get("opponent"),
            projection=projection,
            line=line,
            probability=probability,
            edge=edge,
            confidence=confidence,
            grade=self.det._grade(edge),
            model_version="ml_v1",
        )
