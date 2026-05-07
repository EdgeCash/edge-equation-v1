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

from edge_equation.engines.parlay.builder import (
    ParlayCandidate,
    ParlayLeg,
    build_parlay_candidates,
)
from edge_equation.engines.parlay.config import ParlayConfig

from ..base import ParlayEngine


def _single_leg_ev(leg: ParlayLeg) -> float:
    """Expected return per 1u risked = decimal_odds * p - 1.

    The de-dup tiebreak: when two legs sit in the same correlation
    group, keep the one with the higher single-leg EV.
    """
    return leg.decimal_odds * leg.side_probability - 1.0


def _dedup_key(leg: ParlayLeg) -> str:
    """Group identifier: player_id when present, else game_id.

    Returns an empty string for legs with neither set; those bypass
    de-dup (each treated as its own group).
    """
    if leg.player_id:
        return f"player:{leg.player_id}"
    if leg.game_id:
        return f"game:{leg.game_id}"
    return ""


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
        # Bucket and reduce.
        best_by_key: dict[str, ParlayLeg] = {}
        ungrouped: list[ParlayLeg] = []
        for leg in legs:
            key = _dedup_key(leg)
            if not key:
                ungrouped.append(leg)
                continue
            current = best_by_key.get(key)
            if current is None or _single_leg_ev(leg) > _single_leg_ev(current):
                best_by_key[key] = leg
        deduped = list(best_by_key.values()) + ungrouped
        return build_parlay_candidates(deduped, config=config)
