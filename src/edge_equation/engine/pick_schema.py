"""
Deterministic Pick schema.

A Pick is the fully-populated output of the betting engine for a single market.
It holds the math result (fair_prob or expected_value), the market line,
and the calibration output (edge, kelly, grade, realization).

Picks are frozen after construction to preserve determinism -- downstream
formatters and publishers must not mutate them.
"""
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any, Optional


@dataclass(frozen=True)
class Line:
    """A market line: price (American odds) + optional number (spread/total)."""
    odds: int
    number: Optional[Decimal] = None

    def to_dict(self) -> dict:
        return {
            "odds": self.odds,
            "number": str(self.number) if self.number is not None else None,
        }


@dataclass(frozen=True)
class Pick:
    sport: str
    market_type: str
    selection: str
    line: Line
    fair_prob: Optional[Decimal] = None
    expected_value: Optional[Decimal] = None
    edge: Optional[Decimal] = None
    kelly: Optional[Decimal] = None
    grade: str = "C"
    realization: int = 47
    game_id: Optional[str] = None
    event_time: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "market_type": self.market_type,
            "selection": self.selection,
            "line": self.line.to_dict(),
            "fair_prob": str(self.fair_prob) if self.fair_prob is not None else None,
            "expected_value": str(self.expected_value) if self.expected_value is not None else None,
            "edge": str(self.edge) if self.edge is not None else None,
            "kelly": str(self.kelly) if self.kelly is not None else None,
            "grade": self.grade,
            "realization": self.realization,
            "game_id": self.game_id,
            "event_time": self.event_time,
            "metadata": dict(self.metadata),
        }
