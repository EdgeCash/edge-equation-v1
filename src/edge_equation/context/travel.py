"""
Travel context.

Distance traveled by the away team and timezone changes both hurt the away
side, so both terms push home_adv_delta positive. All miles and tz hours are
treated as absolute magnitudes.
"""
from dataclasses import dataclass
from decimal import Decimal

from edge_equation.context.adjustment import ContextAdjustment


MILES_COEFFICIENT = Decimal('0.0001')  # per away-team mile, per sport (flat)

TZ_COEFFICIENT_PER_HOUR = {
    "NBA": Decimal('0.080'),
    "NFL": Decimal('0.150'),
    "NHL": Decimal('0.050'),
    "MLB": Decimal('0.030'),
    "SOCCER": Decimal('0.060'),
}


@dataclass(frozen=True)
class TravelContext:
    sport: str
    away_travel_miles: float
    timezone_change_hours: int = 0


class TravelAdjuster:
    """
    Away-team travel penalty -> positive home_adv_delta:
    - miles term: |miles| * MILES_COEFFICIENT
    - tz term:    |tz_hours| * per-sport coefficient
    totals_delta is always 0 (travel affects matchup tilt, not scoring level).
    """

    @staticmethod
    def adjustment(ctx: TravelContext) -> ContextAdjustment:
        miles = Decimal(str(abs(ctx.away_travel_miles)))
        tz_hours = Decimal(abs(int(ctx.timezone_change_hours)))
        miles_term = miles * MILES_COEFFICIENT
        tz_coef = TZ_COEFFICIENT_PER_HOUR.get(ctx.sport, Decimal('0'))
        tz_term = tz_coef * tz_hours
        delta = (miles_term + tz_term).quantize(Decimal('0.000001'))
        return ContextAdjustment(
            home_adv_delta=delta,
            totals_delta=Decimal('0').quantize(Decimal('0.000001')),
            components={
                "source": "travel",
                "miles_term": str(miles_term.quantize(Decimal('0.000001'))),
                "tz_term": str(tz_term.quantize(Decimal('0.000001'))),
            },
        )
