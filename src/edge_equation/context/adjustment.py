"""
Shared ContextAdjustment type.

Each adjuster's adjustment() returns a ContextAdjustment with two deltas:
- home_adv_delta: added to the home-field-advantage term (units: league-native)
- totals_delta:   added to the totals / over-under expectation (units: league-native)

Kept in its own module so individual adjusters can import it without creating
a circular dependency with registry.py.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict


@dataclass(frozen=True)
class ContextAdjustment:
    home_adv_delta: Decimal = Decimal('0')
    totals_delta: Decimal = Decimal('0')
    components: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "home_adv_delta": str(self.home_adv_delta),
            "totals_delta": str(self.totals_delta),
            "components": dict(self.components),
        }
