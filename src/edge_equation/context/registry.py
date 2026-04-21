"""
Context registry.

ContextBundle: an optional-valued dataclass holding every context source.
ContextRegistry.compose: sums all active adjusters into a single
ContextAdjustment with a per-source breakdown in .components.

Downstream feature builders consume ContextAdjustment.home_adv_delta and
ContextAdjustment.totals_delta directly; the components dict is for audit
and formatters.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from edge_equation.context.adjustment import ContextAdjustment
from edge_equation.context.rest import RestContext, RestAdjuster
from edge_equation.context.travel import TravelContext, TravelAdjuster
from edge_equation.context.weather import WeatherContext, WeatherAdjuster
from edge_equation.context.officials import OfficialsContext, OfficialsAdjuster
from edge_equation.context.situational import SituationalContext, SituationalAdjuster
from edge_equation.context.injuries import InjuriesContext, InjuriesAdjuster


@dataclass(frozen=True)
class ContextBundle:
    """Container for every context source; each field optional."""
    rest: Optional[RestContext] = None
    travel: Optional[TravelContext] = None
    weather: Optional[WeatherContext] = None
    officials: Optional[OfficialsContext] = None
    situational: Optional[SituationalContext] = None
    injuries: Optional[InjuriesContext] = None


class ContextRegistry:
    """
    Compose a ContextBundle into a single ContextAdjustment by summing every
    active adjuster's output. Inactive fields (None) contribute nothing.

    Source order is deterministic: rest, travel, weather, officials,
    situational, injuries -- so the .components dict is reproducible.
    """

    @staticmethod
    def compose(bundle: ContextBundle) -> ContextAdjustment:
        total_home = Decimal('0')
        total_totals = Decimal('0')
        components = {}

        if bundle.rest is not None:
            a = RestAdjuster.adjustment(bundle.rest)
            total_home += a.home_adv_delta
            total_totals += a.totals_delta
            components["rest"] = a.to_dict()

        if bundle.travel is not None:
            a = TravelAdjuster.adjustment(bundle.travel)
            total_home += a.home_adv_delta
            total_totals += a.totals_delta
            components["travel"] = a.to_dict()

        if bundle.weather is not None:
            a = WeatherAdjuster.adjustment(bundle.weather)
            total_home += a.home_adv_delta
            total_totals += a.totals_delta
            components["weather"] = a.to_dict()

        if bundle.officials is not None:
            a = OfficialsAdjuster.adjustment(bundle.officials)
            total_home += a.home_adv_delta
            total_totals += a.totals_delta
            components["officials"] = a.to_dict()

        if bundle.situational is not None:
            a = SituationalAdjuster.adjustment(bundle.situational)
            total_home += a.home_adv_delta
            total_totals += a.totals_delta
            components["situational"] = a.to_dict()

        if bundle.injuries is not None:
            a = InjuriesAdjuster.adjustment(bundle.injuries)
            total_home += a.home_adv_delta
            total_totals += a.totals_delta
            components["injuries"] = a.to_dict()

        return ContextAdjustment(
            home_adv_delta=total_home.quantize(Decimal('0.000001')),
            totals_delta=total_totals.quantize(Decimal('0.000001')),
            components=components,
        )
