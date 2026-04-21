"""
Exponential time decay for historical observations.

Given an age (in days) and a league-specific decay coefficient xi, the weight
of an observation is w = exp(-xi * age_days). Used by upstream strength
builders (offensive/defensive ratings, team form) to down-weight older games.

Per-league xi values were chosen so that the half-life roughly matches the
competitive tempo of each league:
- MLB  xi=0.0025  -> half-life ~277 days
- NHL  xi=0.0040  -> half-life ~173 days
- NFL  xi=0.0040  -> half-life ~173 days
- NBA  xi=0.0055  -> half-life ~126 days
- SOCCER xi=0.0020 -> half-life ~347 days

Smaller xi -> longer memory; larger xi -> faster forgetting.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional
import math


DECAY_XI_REGISTRY = {
    "SOCCER": Decimal('0.0020'),
    "NFL": Decimal('0.0040'),
    "NBA": Decimal('0.0055'),
    "NHL": Decimal('0.0040'),
    "MLB": Decimal('0.0025'),
}


@dataclass(frozen=True)
class DecayParams:
    """Immutable decay configuration for a single league."""
    sport: str
    xi: Decimal

    def halflife_days(self) -> Decimal:
        # halflife = ln(2) / xi
        hl = Decimal(str(math.log(2))) / self.xi
        return hl.quantize(Decimal('0.000001'))

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "xi": str(self.xi),
            "halflife_days": str(self.halflife_days()),
        }


class DecayWeights:
    """
    Exponential decay helpers:
    - for_sport: look up DecayParams for a registered league
    - weight: w = exp(-xi * age_days) for a single observation
    - weighted_mean: decay-weighted mean of (value, age_days) pairs
    - apply: elementwise decay weights for a list of ages
    """

    @staticmethod
    def for_sport(sport: str) -> DecayParams:
        if sport not in DECAY_XI_REGISTRY:
            raise ValueError(
                f"Unknown sport '{sport}' for decay. "
                f"Known: {sorted(DECAY_XI_REGISTRY.keys())}"
            )
        return DecayParams(sport=sport, xi=DECAY_XI_REGISTRY[sport])

    @staticmethod
    def weight(age_days: float, xi: Decimal) -> Decimal:
        if age_days < 0:
            raise ValueError(f"age_days must be >= 0, got {age_days}")
        exponent = -float(xi) * float(age_days)
        w = math.exp(exponent)
        return Decimal(str(w)).quantize(Decimal('0.000001'))

    @staticmethod
    def apply(ages_days: Iterable[float], xi: Decimal) -> list:
        return [DecayWeights.weight(a, xi) for a in ages_days]

    @staticmethod
    def weighted_mean(
        values: Iterable[float],
        ages_days: Iterable[float],
        xi: Decimal,
    ) -> Decimal:
        values_list = list(values)
        ages_list = list(ages_days)
        if len(values_list) != len(ages_list):
            raise ValueError(
                f"values and ages_days must have equal length, "
                f"got {len(values_list)} and {len(ages_list)}"
            )
        if not values_list:
            return Decimal('0').quantize(Decimal('0.000001'))
        weights = DecayWeights.apply(ages_list, xi)
        weight_sum = sum(weights)
        if weight_sum == Decimal('0'):
            return Decimal('0').quantize(Decimal('0.000001'))
        weighted_sum = sum(
            Decimal(str(v)) * w for v, w in zip(values_list, weights)
        )
        return (weighted_sum / weight_sum).quantize(Decimal('0.000001'))
