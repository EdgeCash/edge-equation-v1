"""
Feature builder.

Produces a FeatureBundle that the math layer can consume directly. Validates
sport + market_type against sport_config. Drops unknown universal feature keys.

Phase 7a extensions:
- decay_params (DecayParams): when provided, replaces inputs['strength_home']
  and inputs['strength_away'] with decay-weighted means computed from
  inputs['home_strength_history'] / inputs['away_strength_history'] (lists of
  (value, age_days) tuples). The half-life is recorded in metadata so it can
  surface on the Pick.
- hfa_context (dict): when provided, must contain 'home_team' and may contain
  'venue'. HFACalculator resolves the home-field advantage; the total
  overrides inputs['home_adv'] and is recorded in metadata.

Phase 7b extension:
- context_bundle (ContextBundle): when provided, ContextRegistry.compose sums
  every active adjuster. home_adv_delta is added to inputs['home_adv'] (after
  HFA resolution if present); totals_delta is added to inputs['dixon_coles_adj']
  (initialized to 0 if absent). The full composed adjustment dict is stored
  in metadata for audit.

All three kwargs are strict no-ops when None (existing callers unchanged).
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from edge_equation.math.stats import DeterministicStats
from edge_equation.math.decay import DecayParams, DecayWeights
from edge_equation.math.hfa import HFACalculator
from edge_equation.context.registry import ContextBundle, ContextRegistry
from edge_equation.config.sport_config import SPORT_CONFIG


META_DECAY_HALFLIFE_KEY = "phase7a_decay_halflife_days"
META_HFA_VALUE_KEY = "phase7a_hfa_value"
META_CONTEXT_ADJUSTMENT_KEY = "phase7b_context_adjustment"


RATE_PROP_MARKETS = {
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
}
ML_MARKETS = {"ML", "Run_Line", "Puck_Line", "Spread"}
TOTAL_MARKETS = {"Total", "Game_Total"}
BTTS_MARKETS = {"BTTS"}
PASSTHROUGH_MARKETS = {"NRFI", "YRFI"}


@dataclass
class FeatureBundle:
    sport: str
    market_type: str
    inputs: dict
    universal_features: dict
    game_id: Optional[str] = None
    event_time: Optional[str] = None
    selection: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "market_type": self.market_type,
            "inputs": dict(self.inputs),
            "universal_features": dict(self.universal_features),
            "game_id": self.game_id,
            "event_time": self.event_time,
            "selection": self.selection,
            "metadata": dict(self.metadata),
        }


class FeatureBuilder:
    @staticmethod
    def _validate_sport_market(sport: str, market_type: str) -> None:
        if sport not in SPORT_CONFIG:
            raise ValueError(f"Unknown sport: {sport}")
        allowed = SPORT_CONFIG[sport]["markets"]
        if market_type not in allowed:
            raise ValueError(
                f"Market '{market_type}' not supported for sport '{sport}'. "
                f"Allowed: {allowed}"
            )

    @staticmethod
    def _normalize_universal(raw: dict) -> dict:
        clean = {}
        for k in DeterministicStats.UNIVERSAL_KEYS:
            if k in raw:
                clean[k] = float(raw[k])
        return clean

    @staticmethod
    def _validate_inputs(market_type: str, inputs: dict) -> None:
        if market_type in ML_MARKETS:
            required = ["strength_home", "strength_away"]
        elif market_type in TOTAL_MARKETS:
            required = ["off_env", "def_env", "pace"]
        elif market_type in RATE_PROP_MARKETS:
            required = ["rate"]
        elif market_type in BTTS_MARKETS:
            required = ["home_lambda", "away_lambda"]
        elif market_type in PASSTHROUGH_MARKETS:
            required = []
        else:
            raise ValueError(f"Unsupported market_type: {market_type}")
        missing = [k for k in required if k not in inputs]
        if missing:
            raise ValueError(
                f"Missing required inputs for market '{market_type}': {missing}"
            )

    @staticmethod
    def _apply_decay(inputs: dict, decay_params: DecayParams, metadata: dict) -> None:
        """
        If history lists are present, replace strength_home/strength_away with
        decay-weighted means. Records halflife in metadata for downstream
        propagation onto the Pick.
        """
        home_hist = inputs.get("home_strength_history")
        away_hist = inputs.get("away_strength_history")
        if home_hist is not None:
            values = [float(v) for v, _ in home_hist]
            ages = [float(a) for _, a in home_hist]
            weighted = DecayWeights.weighted_mean(values, ages, decay_params.xi)
            inputs["strength_home"] = float(weighted)
        if away_hist is not None:
            values = [float(v) for v, _ in away_hist]
            ages = [float(a) for _, a in away_hist]
            weighted = DecayWeights.weighted_mean(values, ages, decay_params.xi)
            inputs["strength_away"] = float(weighted)
        metadata[META_DECAY_HALFLIFE_KEY] = str(decay_params.halflife_days())

    @staticmethod
    def _apply_hfa(sport: str, inputs: dict, hfa_context: dict, metadata: dict) -> None:
        """
        Resolve HFA via HFACalculator. 'home_team' is required in hfa_context;
        'venue' is optional. The resolved total overrides inputs['home_adv'] and
        is recorded in metadata.
        """
        home_team = hfa_context.get("home_team")
        if home_team is None:
            raise ValueError("hfa_context requires 'home_team'")
        venue = hfa_context.get("venue")
        ctx = {"venue": venue} if venue is not None else None
        hfa = HFACalculator.get_home_adv(sport, team=home_team, context=ctx)
        inputs["home_adv"] = float(hfa.total)
        metadata[META_HFA_VALUE_KEY] = str(hfa.total)

    @staticmethod
    def _apply_context(inputs: dict, context_bundle: ContextBundle, metadata: dict) -> None:
        """
        Compose all active context adjusters and merge into inputs:
        - home_adv_delta -> added to inputs['home_adv'] (after HFA if present)
        - totals_delta   -> added to inputs['dixon_coles_adj'] (created if absent)
        The composed ContextAdjustment.to_dict() is stored in metadata.
        """
        adj = ContextRegistry.compose(context_bundle)
        if "home_adv" in inputs:
            inputs["home_adv"] = float(Decimal(str(inputs["home_adv"])) + adj.home_adv_delta)
        else:
            inputs["home_adv"] = float(adj.home_adv_delta)
        inputs["dixon_coles_adj"] = float(
            Decimal(str(inputs.get("dixon_coles_adj", 0.0))) + adj.totals_delta
        )
        metadata[META_CONTEXT_ADJUSTMENT_KEY] = adj.to_dict()

    @staticmethod
    def build(
        sport: str,
        market_type: str,
        inputs: dict,
        universal_features: Optional[dict] = None,
        game_id: Optional[str] = None,
        event_time: Optional[str] = None,
        selection: Optional[str] = None,
        metadata: Optional[dict] = None,
        decay_params: Optional[DecayParams] = None,
        hfa_context: Optional[dict] = None,
        context_bundle: Optional[ContextBundle] = None,
    ) -> FeatureBundle:
        FeatureBuilder._validate_sport_market(sport, market_type)
        mutable_inputs = dict(inputs)
        mutable_meta = dict(metadata or {})
        if decay_params is not None:
            FeatureBuilder._apply_decay(mutable_inputs, decay_params, mutable_meta)
        if hfa_context is not None:
            FeatureBuilder._apply_hfa(sport, mutable_inputs, hfa_context, mutable_meta)
        if context_bundle is not None:
            FeatureBuilder._apply_context(mutable_inputs, context_bundle, mutable_meta)
        # Drop history lists before validation/storage -- they are transient.
        mutable_inputs.pop("home_strength_history", None)
        mutable_inputs.pop("away_strength_history", None)
        FeatureBuilder._validate_inputs(market_type, mutable_inputs)
        clean_univ = FeatureBuilder._normalize_universal(universal_features or {})
        return FeatureBundle(
            sport=sport,
            market_type=market_type,
            inputs=mutable_inputs,
            universal_features=clean_univ,
            game_id=game_id,
            event_time=event_time,
            selection=selection,
            metadata=mutable_meta,
        )

    @staticmethod
    def sport_weights(sport: str) -> dict:
        if sport not in SPORT_CONFIG:
            raise ValueError(f"Unknown sport: {sport}")
        cfg = SPORT_CONFIG[sport]
        return {
            "league_baseline_total": cfg["league_baseline_total"],
            "ml_universal_weight": cfg["ml_universal_weight"],
            "prop_universal_weight": cfg["prop_universal_weight"],
        }
