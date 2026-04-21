"""
Officials / crew-tendency context.

Crew tendencies are pre-computed upstream (e.g. rolling average of totals vs.
market by crew over the last N games) and passed in as crew_total_delta.
This adjuster simply attaches that delta to totals_delta -- no further math.

home_adv_delta is always 0 -- officiating asymmetry is not modeled here.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from edge_equation.context.adjustment import ContextAdjustment


@dataclass(frozen=True)
class OfficialsContext:
    sport: str
    crew_id: Optional[str] = None
    crew_total_delta: Decimal = Decimal('0')


class OfficialsAdjuster:
    """
    Pass-through of a pre-computed crew total delta:
    - home_adv_delta = 0
    - totals_delta   = ctx.crew_total_delta
    """

    @staticmethod
    def adjustment(ctx: OfficialsContext) -> ContextAdjustment:
        return ContextAdjustment(
            home_adv_delta=Decimal('0').quantize(Decimal('0.000001')),
            totals_delta=ctx.crew_total_delta.quantize(Decimal('0.000001')),
            components={
                "source": "officials",
                "crew_id": ctx.crew_id,
                "crew_total_delta": str(ctx.crew_total_delta),
            },
        )
