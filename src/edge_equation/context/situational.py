"""
Situational context: back-to-back, look-ahead, revenge.

Flags hit the home side as positive or negative home_adv_delta:
- home_b2b       : penalty against home (home_adv_delta += B2B_PENALTY, negative)
- away_b2b       : bonus to home       (home_adv_delta -= B2B_PENALTY)
- home_look_ahead: penalty against home
- away_look_ahead: bonus to home
- home_revenge   : bonus to home
- away_revenge   : penalty against home

B2B penalty is sport-specific (biggest in NBA). Look-ahead and revenge are
treated as league-agnostic effects.
"""
from dataclasses import dataclass
from decimal import Decimal

from edge_equation.context.adjustment import ContextAdjustment


B2B_PENALTY = {
    "NBA": Decimal('-0.120'),
    "NHL": Decimal('-0.050'),
    "MLB": Decimal('-0.020'),
}
LOOK_AHEAD_PENALTY = Decimal('-0.050')
REVENGE_BONUS = Decimal('0.040')


@dataclass(frozen=True)
class SituationalContext:
    sport: str
    home_b2b: bool = False
    away_b2b: bool = False
    home_look_ahead: bool = False
    away_look_ahead: bool = False
    home_revenge: bool = False
    away_revenge: bool = False


class SituationalAdjuster:
    """
    Sum of per-flag effects into home_adv_delta. totals_delta is always 0.
    Unknown sport yields zero for b2b; look-ahead / revenge still apply.
    """

    @staticmethod
    def adjustment(ctx: SituationalContext) -> ContextAdjustment:
        delta = Decimal('0')
        b2b = B2B_PENALTY.get(ctx.sport, Decimal('0'))
        if ctx.home_b2b:
            delta += b2b
        if ctx.away_b2b:
            delta -= b2b
        if ctx.home_look_ahead:
            delta += LOOK_AHEAD_PENALTY
        if ctx.away_look_ahead:
            delta -= LOOK_AHEAD_PENALTY
        if ctx.home_revenge:
            delta += REVENGE_BONUS
        if ctx.away_revenge:
            delta -= REVENGE_BONUS

        return ContextAdjustment(
            home_adv_delta=delta.quantize(Decimal('0.000001')),
            totals_delta=Decimal('0').quantize(Decimal('0.000001')),
            components={
                "source": "situational",
                "home_b2b": ctx.home_b2b,
                "away_b2b": ctx.away_b2b,
                "home_look_ahead": ctx.home_look_ahead,
                "away_look_ahead": ctx.away_look_ahead,
                "home_revenge": ctx.home_revenge,
                "away_revenge": ctx.away_revenge,
            },
        )
