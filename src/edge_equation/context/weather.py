"""
Weather context.

Weather affects scoring totals in outdoor sports:
- Wind above 10 mph depresses MLB / NFL / SOCCER scoring.
- Cold below 40F depresses MLB / NFL scoring further.
- Precipitation depresses all outdoor scoring modestly.

Indoor sports (NBA, NHL) and unknown sports return a zero adjustment. No
home_adv_delta component -- weather symmetrically affects both teams.
"""
from dataclasses import dataclass
from decimal import Decimal

from edge_equation.context.adjustment import ContextAdjustment


WIND_THRESHOLD_MPH = Decimal('10')
COLD_THRESHOLD_F = Decimal('40')

WIND_COEF = {
    "MLB": Decimal('-0.080'),
    "NFL": Decimal('-0.150'),
    "SOCCER": Decimal('-0.020'),
}
COLD_COEF = {
    "MLB": Decimal('-0.030'),
    "NFL": Decimal('-0.050'),
}
PRECIP_COEF = {
    "MLB": Decimal('-0.020'),
    "NFL": Decimal('-0.040'),
    "SOCCER": Decimal('-0.010'),
}


@dataclass(frozen=True)
class WeatherContext:
    sport: str
    temperature_f: float = 70.0
    wind_mph: float = 0.0
    precipitation_pct: float = 0.0  # 0-100


class WeatherAdjuster:
    """
    Totals depression for outdoor sports:
    - wind above 10 mph contributes (wind-10) * per-sport coefficient
    - temperature below 40F contributes (40-T)/10 * per-sport coefficient
    - precipitation (0-100) contributes (pct/100) * per-sport coefficient
    home_adv_delta is always 0.
    """

    @staticmethod
    def adjustment(ctx: WeatherContext) -> ContextAdjustment:
        wind = Decimal(str(ctx.wind_mph))
        temp = Decimal(str(ctx.temperature_f))
        precip = Decimal(str(ctx.precipitation_pct))

        wind_term = Decimal('0')
        if wind > WIND_THRESHOLD_MPH:
            wind_term = WIND_COEF.get(ctx.sport, Decimal('0')) * (wind - WIND_THRESHOLD_MPH)

        cold_term = Decimal('0')
        if temp < COLD_THRESHOLD_F:
            cold_term = COLD_COEF.get(ctx.sport, Decimal('0')) * (COLD_THRESHOLD_F - temp) / Decimal('10')

        precip_term = PRECIP_COEF.get(ctx.sport, Decimal('0')) * (precip / Decimal('100'))

        totals = (wind_term + cold_term + precip_term).quantize(Decimal('0.000001'))

        return ContextAdjustment(
            home_adv_delta=Decimal('0').quantize(Decimal('0.000001')),
            totals_delta=totals,
            components={
                "source": "weather",
                "wind_term": str(wind_term.quantize(Decimal('0.000001'))),
                "cold_term": str(cold_term.quantize(Decimal('0.000001'))),
                "precip_term": str(precip_term.quantize(Decimal('0.000001'))),
            },
        )
