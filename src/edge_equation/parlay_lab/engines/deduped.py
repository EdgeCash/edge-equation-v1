"""Same-game / same-player de-duped engine.

Hypothesis under test: a sizeable share of qualifying legs on any
slate are correlated siblings (two run-line legs in the same matchup,
or two prop legs from the same player). The Gaussian copula MC step
mostly punishes those at scoring time, but they still inflate the
combinatorial search space.

Strategy: before enumeration, group legs by ``player_id`` (when set)
and otherwise by ``game_id``, keep the highest-EV leg per group, and
then call the standard builder. If this engine matches or beats the
baseline on ROI / calibration, the baseline is leaving information on
the table by including correlated siblings. If it loses, the copula
is doing its job and the siblings carry useful diversity.
"""

from __future__ import annotations

from edge_equation.engines.parlay.builder import ParlayCandidate, ParlayLeg
from edge_equation.engines.parlay.config import ParlayConfig
from edge_equation.engines.parlay.strategies import (
    _dedup_key, _single_leg_ev, build_deduped,
)

from ..base import ParlayEngine


# Re-exported for tests that introspect the dedup keys directly.
__all__ = ["SameGameDedupedEngine", "_dedup_key", "_single_leg_ev"]


class SameGameDedupedEngine(ParlayEngine):
    name = "deduped"
    description = (
        "Group qualifying legs by player_id (else game_id), keep the "
        "highest-single-leg-EV leg per group, then run the standard "
        "builder. Tests whether correlated-sibling stacking dilutes "
        "EV vs. the baseline."
    )

    def build(
        self,
        legs: list[ParlayLeg],
        config: ParlayConfig,
    ) -> list[ParlayCandidate]:
        return build_deduped(legs, config)
