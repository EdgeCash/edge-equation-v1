"""
Betting engine.

Glue layer that takes a FeatureBundle + market Line and produces a Pick.
"""
from decimal import Decimal
from typing import Optional

from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.scoring import ConfidenceScorer
from edge_equation.engine.feature_builder import (
    FeatureBundle,
    META_DECAY_HALFLIFE_KEY,
    META_HFA_VALUE_KEY,
)
from edge_equation.engine.pick_schema import Pick, Line


PROB_MARKETS = {"ML", "Run_Line", "Puck_Line", "Spread", "BTTS"}
EXPECTATION_MARKETS = {
    "Total", "Game_Total",
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
}


class BettingEngine:

    @staticmethod
    def evaluate(
        bundle: FeatureBundle,
        line: Line,
        public_mode: bool = False,
    ) -> Pick:
        market = bundle.market_type
        sport = bundle.sport
        selection = bundle.selection or ""

        fv = ProbabilityCalculator.calculate_fair_value(
            market, sport, bundle.inputs, bundle.universal_features
        )

        fair_prob: Optional[Decimal] = None
        expected_value: Optional[Decimal] = None
        edge: Optional[Decimal] = None
        kelly: Optional[Decimal] = None
        grade = "C"
        realization = 47

        if market in PROB_MARKETS:
            fair_prob = fv.get("fair_prob")
            calib = EVCalculator.calibrate(
                public_mode,
                {"fair_prob": fair_prob},
                {"odds": line.odds},
            )
            edge = calib["edge"]
            kelly = calib["kelly"]
            if not public_mode and edge is not None:
                grade = ConfidenceScorer.grade(edge)
                realization = ConfidenceScorer.realization_for_grade(grade)

        elif market in EXPECTATION_MARKETS:
            if "expected_total" in fv:
                expected_value = fv["expected_total"]
            elif "expected_value" in fv:
                expected_value = fv["expected_value"]
            edge = None
            kelly = None

        else:
            raise ValueError(f"BettingEngine: unsupported market {market}")

        halflife_raw = bundle.metadata.get(META_DECAY_HALFLIFE_KEY)
        hfa_raw = bundle.metadata.get(META_HFA_VALUE_KEY)
        decay_halflife_days = Decimal(halflife_raw) if halflife_raw is not None else None
        hfa_value = Decimal(hfa_raw) if hfa_raw is not None else None

        return Pick(
            sport=sport,
            market_type=market,
            selection=selection,
            line=line,
            fair_prob=fair_prob,
            expected_value=expected_value,
            edge=edge,
            kelly=kelly,
            grade=grade,
            realization=realization,
            game_id=bundle.game_id,
            event_time=bundle.event_time,
            decay_halflife_days=decay_halflife_days,
            hfa_value=hfa_value,
            metadata={
                "raw_universal_sum": str(fv.get("raw_universal_sum"))
                    if fv.get("raw_universal_sum") is not None else None,
                **dict(bundle.metadata),
            },
        )
