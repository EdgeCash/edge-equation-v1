"""
Rest-day context.

A positive (home_rest - away_rest) diff tilts home_adv_delta positive by a
sport-specific coefficient. Back-to-back handling lives in situational.py;
this module only scales the raw rest-day differential.
"""
from dataclasses import dataclass
from decimal import Decimal

from edge_equation.context.adjustment import ContextAdjustment


REST_COEFFICIENTS = {
    "NBA": Decimal('0.040'),
    "NFL": Decimal('0.100'),
    "NHL": Decimal('0.020'),
    "MLB": Decimal('0.015'),
    "SOCCER": Decimal('0.030'),
}


@dataclass(frozen=True)
class RestContext:
    sport: str
    home_rest_days: int
    away_rest_days: int


class RestAdjuster:
    """
    Rest-day differential:
    - home_adv_delta = (home_rest_days - away_rest_days) * sport_coefficient
    - totals_delta   = 0
    Unknown sport yields a zero adjustment (no crash).
    """

    @staticmethod
    def adjustment(ctx: RestContext) -> ContextAdjustment:
        coef = REST_COEFFICIENTS.get(ctx.sport, Decimal('0'))
        diff = Decimal(int(ctx.home_rest_days - ctx.away_rest_days))
        delta = (coef * diff).quantize(Decimal('0.000001'))
        return ContextAdjustment(
            home_adv_delta=delta,
            totals_delta=Decimal('0').quantize(Decimal('0.000001')),
            components={
                "source": "rest",
                "diff_days": int(ctx.home_rest_days - ctx.away_rest_days),
                "coefficient": str(coef),
            },
        )
