"""Engine ABC + lightweight result types.

Every engine variant lives behind one method::

    def build(self, legs: list[ParlayLeg], config: ParlayConfig)
        -> list[ParlayCandidate]

The shootout calls this on the same input slate and grades the
output against historical actuals. New engines drop in by
subclassing :class:`ParlayEngine` and registering in
``engines/__init__.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from edge_equation.engines.parlay.builder import (
    ParlayCandidate,
    ParlayLeg,
)
from edge_equation.engines.parlay.config import ParlayConfig


class ParlayEngine(ABC):
    """One parlay-construction strategy.

    Subclasses set ``name`` (CLI key) and ``description`` (one-liner
    for the leaderboard). Implement :meth:`build` to consume a leg
    pool and return ranked candidates.
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def build(
        self,
        legs: list[ParlayLeg],
        config: ParlayConfig,
    ) -> list[ParlayCandidate]:
        """Return the list of candidate parlays for this slate.

        ``legs`` is already gate-filtered (tier, edge, joint-prob
        floor). The engine's job is purely to combine them.
        """
        raise NotImplementedError


@dataclass(frozen=True)
class GradedLeg:
    """A historical leg with its observed outcome attached.

    The shootout grades each candidate parlay by AND-ing every leg's
    grade. Pushes are handled per ``push_policy`` in
    :func:`grade_parlay`.
    """
    leg: ParlayLeg
    result: str               # 'WIN' / 'LOSS' / 'PUSH'
    decimal_odds: float       # actual closing price when known, else fallback
    pick_id: str              # stable id for de-dup checks across engines


@dataclass(frozen=True)
class GradedSlate:
    """One day's leg pool + actuals, fed to every engine in turn."""
    date: str                 # YYYY-MM-DD
    graded_legs: tuple[GradedLeg, ...]

    @property
    def legs(self) -> list[ParlayLeg]:
        return [g.leg for g in self.graded_legs]

    def lookup(self, leg: ParlayLeg) -> Optional[GradedLeg]:
        """Find this leg's graded record by stable identity."""
        for g in self.graded_legs:
            if (
                g.leg.market_type == leg.market_type
                and g.leg.side == leg.side
                and g.leg.game_id == leg.game_id
                and g.leg.player_id == leg.player_id
            ):
                return g
        return None
