"""Diversified engine -- enforce minimum cross-game spread.

Hypothesis under test: parlays whose legs span more games are less
correlated and bust less often. The deduped engine handles same-game
de-dup at the leg pool stage; this engine goes further and rejects
*combos* whose legs come from too few distinct games even if each
game contributes only one leg.

Mechanism: identical to the baseline enumeration, but reject combos
whose distinct ``game_id`` count is below ``min_distinct_games``
(default 3 for max_legs >= 3, scaled down for smaller parlays). The
filter applies BEFORE the expensive copula MC, so the cost of
diversification is just a couple of set-counts per combo.

Two-leg parlays are always allowed (can't have 3 distinct games in
2 legs); the constraint only kicks in at >= 3 legs.
"""

from __future__ import annotations

import itertools
import os
from typing import Sequence

from edge_equation.engines.parlay.builder import (
    ParlayCandidate,
    ParlayLeg,
    _candidate_for_combo,
    _passes_min_tier,
)
from edge_equation.engines.parlay.config import ParlayConfig

from ..base import ParlayEngine


def _min_games_required(n_legs: int) -> int:
    """Diversification floor scaled with parlay size.

    2-leg: 2 (i.e. no constraint beyond same-game-block which is already
    handled by ``_combo_is_compatible``).
    3-leg: 3 distinct games.
    4-leg: 3 distinct games.
    5+ leg: 4 distinct games.
    """
    if n_legs <= 2:
        return n_legs
    if n_legs <= 4:
        return 3
    return 4


def _override_floor() -> int | None:
    raw = os.environ.get("PARLAY_LAB_MIN_DISTINCT_GAMES", "")
    if raw:
        try:
            return max(2, int(raw))
        except ValueError:
            return None
    return None


def _distinct_game_count(combo: Sequence[ParlayLeg]) -> int:
    return len({l.game_id for l in combo if l.game_id})


class DiversifiedEngine(ParlayEngine):
    name = "diversified"
    description = (
        "Enumerate as the baseline, but reject combos with fewer than "
        "min(3, n_legs) distinct game_ids before scoring. Tests "
        "whether cross-game diversification beats concentration on "
        "ROI / drawdown."
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
        floor_override = _override_floor()
        candidates: list[ParlayCandidate] = []
        for n in range(2, config.max_legs + 1):
            min_games = floor_override if floor_override else _min_games_required(n)
            for combo in itertools.combinations(pool, n):
                if _distinct_game_count(combo) < min_games:
                    continue
                cand = _candidate_for_combo(combo, config=config)
                if cand is not None:
                    candidates.append(cand)
        candidates.sort(key=lambda c: c.ev_units, reverse=True)
        return candidates
