"""
PremiumPick.

Immutable wrapper around an existing Pick. Adds distribution quantiles
from the Monte Carlo simulator and a free-form notes field. Does not
modify the underlying Pick schema.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from edge_equation.engine.pick_schema import Pick


@dataclass(frozen=True)
class PremiumPick:
    base_pick: Pick
    p10: Optional[Decimal] = None
    p50: Optional[Decimal] = None
    p90: Optional[Decimal] = None
    mean: Optional[Decimal] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "base_pick": self.base_pick.to_dict(),
            "p10": str(self.p10) if self.p10 is not None else None,
            "p50": str(self.p50) if self.p50 is not None else None,
            "p90": str(self.p90) if self.p90 is not None else None,
            "mean": str(self.mean) if self.mean is not None else None,
            "notes": self.notes,
        }
