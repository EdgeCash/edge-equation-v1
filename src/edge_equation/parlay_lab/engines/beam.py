"""Beam-search engine -- approximate optimum without enumeration.

Hypothesis under test: the top-N candidates we'd find via full
combinatorial enumeration are reachable via greedy expansion of the
top single-leg-EV pairs. If true, we get near-baseline ROI without
the C(N, max_legs) explosion.

Mechanism:

1. Score every 2-leg combo via the same `_candidate_for_combo`
   (Gaussian-copula MC + gate). Keep the top ``beam_width`` by EV.
2. For each surviving beam, try extending it by every other leg in
   the pool. Score each extension. Keep the new top ``beam_width``.
3. Repeat until the beams reach ``max_legs``.
4. Return the union of all qualifying beams seen across stages.

Cost: O(N * max_legs * beam_width) candidate evaluations vs.
O(N choose max_legs) for the baseline. With N=20 and max_legs=4,
that's ~2,400 vs 4,845 -- close to break-even at the cap. With
N=60 and max_legs=4 it's ~7,200 vs 487,635 -- 67x cheaper.

Configurable via the ``BEAM_WIDTH`` env override; defaults to 30
(generous enough that we don't drop a near-optimal expansion).
"""

from __future__ import annotations

import itertools
import os
from typing import Optional

from edge_equation.engines.parlay.builder import (
    ParlayCandidate,
    ParlayLeg,
    _candidate_for_combo,
    _passes_min_tier,
)
from edge_equation.engines.parlay.config import ParlayConfig

from ..base import ParlayEngine


_DEFAULT_BEAM_WIDTH: int = 30


def _beam_width() -> int:
    raw = os.environ.get("PARLAY_LAB_BEAM_WIDTH", "")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            return _DEFAULT_BEAM_WIDTH
    return _DEFAULT_BEAM_WIDTH


def _legs_id(legs) -> tuple:
    """Stable hashable id for a leg set, regardless of order."""
    return tuple(sorted(
        (l.market_type, l.side, l.game_id or "", l.player_id or "")
        for l in legs
    ))


class BeamEngine(ParlayEngine):
    name = "beam"
    description = (
        "Greedy beam search: top-K 2-leg combos by EV, expanded one "
        "leg at a time up to max_legs, keeping top-K survivors per "
        "stage. Tests whether near-optimal is reachable without "
        "exhaustive enumeration."
    )

    def build(
        self,
        legs: list[ParlayLeg],
        config: ParlayConfig,
    ) -> list[ParlayCandidate]:
        pool = [l for l in legs if _passes_min_tier(l, config.min_tier)]
        if len(pool) > config.max_pool_size:
            pool.sort(
                key=lambda l: l.decimal_odds * l.side_probability,
                reverse=True,
            )
            pool = pool[: config.max_pool_size]
        if config.max_legs < 2:
            return []

        beam_width = _beam_width()

        # Stage 0: 2-leg seeds.
        beams: list[ParlayCandidate] = []
        seen_ids: set[tuple] = set()
        for combo in itertools.combinations(pool, 2):
            cand = _candidate_for_combo(combo, config=config)
            if cand is not None:
                beams.append(cand)
        beams.sort(key=lambda c: c.ev_units, reverse=True)
        beams = beams[:beam_width]
        all_candidates: list[ParlayCandidate] = list(beams)
        for c in beams:
            seen_ids.add(_legs_id(c.legs))

        # Stages 3..max_legs: extend each beam by one new leg.
        for n in range(3, config.max_legs + 1):
            extended: list[ParlayCandidate] = []
            for beam in beams:
                used = set(beam.legs)
                for new_leg in pool:
                    if new_leg in used:
                        continue
                    new_combo = (*beam.legs, new_leg)
                    new_id = _legs_id(new_combo)
                    if new_id in seen_ids:
                        continue
                    cand = _candidate_for_combo(new_combo, config=config)
                    if cand is None:
                        continue
                    extended.append(cand)
                    seen_ids.add(new_id)
            extended.sort(key=lambda c: c.ev_units, reverse=True)
            beams = extended[:beam_width]
            all_candidates.extend(beams)

        all_candidates.sort(key=lambda c: c.ev_units, reverse=True)
        return all_candidates
