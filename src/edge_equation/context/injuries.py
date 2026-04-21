"""
Injury impact context.

Each side's aggregated injury impact is a Decimal in [0, 1] where 0 is fully
healthy and 1 is devastated. The adjuster tilts home_adv_delta toward whichever
side is relatively healthier, scaled by a sport-specific magnitude (biggest in
NBA since star players carry outsized share of team value).
"""
from dataclasses import dataclass
from decimal import Decimal

from edge_equation.context.adjustment import ContextAdjustment


INJURY_SPORT_SCALE = {
    "NBA": Decimal('3.000'),
    "NFL": Decimal('2.000'),
    "NHL": Decimal('0.300'),
    "MLB": Decimal('0.200'),
    "SOCCER": Decimal('0.400'),
}


@dataclass(frozen=True)
class InjuriesContext:
    sport: str
    home_injury_impact: Decimal = Decimal('0')
    away_injury_impact: Decimal = Decimal('0')


class InjuriesAdjuster:
    """
    Injury differential drives home_adv_delta:
    - home_adv_delta = (away_impact - home_impact) * sport_scale
    - totals_delta = 0 (injuries tilt matchup, not total scoring level)
    Input impacts are expected in [0, 1] but not clamped -- caller's contract.
    """

    @staticmethod
    def adjustment(ctx: InjuriesContext) -> ContextAdjustment:
        scale = INJURY_SPORT_SCALE.get(ctx.sport, Decimal('0'))
        diff = ctx.away_injury_impact - ctx.home_injury_impact
        delta = (diff * scale).quantize(Decimal('0.000001'))
        return ContextAdjustment(
            home_adv_delta=delta,
            totals_delta=Decimal('0').quantize(Decimal('0.000001')),
            components={
                "source": "injuries",
                "home_impact": str(ctx.home_injury_impact),
                "away_impact": str(ctx.away_injury_impact),
                "scale": str(scale),
            },
        )
