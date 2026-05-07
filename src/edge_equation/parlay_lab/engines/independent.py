"""Independent engine -- skip the Gaussian-copula MC step.

Hypothesis under test: the copula correlation adjustment is the most
expensive thing in the parlay engine (10k MC trials per combo). If
the leaderboard says it doesn't move ROI / calibration enough to
justify the cost, we can drop it and run 100x faster.

Mechanism: same combinatorial sweep as the baseline, but
``joint_prob_corr`` is set to the simple product of leg
probabilities (independence assumption). Same gate, same EV math,
same scoring -- just a cheaper joint-prob estimate.

Fair comparison note: the baseline still wins on calibration if and
only if leg correlations are real and consistently signed. If the
backfill says "Brier is the same with and without," correlations
either cancel out or are too small to matter.
"""

from __future__ import annotations

import itertools
from typing import Optional, Sequence

import numpy as np

from edge_equation.engines.parlay.builder import (
    ParlayCandidate,
    ParlayLeg,
    _combo_is_compatible,
    _passes_min_tier,
    expected_value_units,
    qualify_parlay,
)
from edge_equation.engines.parlay.config import ParlayConfig

from ..base import ParlayEngine


def _candidate_under_independence(
    combo: Sequence[ParlayLeg], *, config: ParlayConfig,
) -> Optional[ParlayCandidate]:
    """Mirror of ``_candidate_for_combo`` with no MC step.

    ``joint_prob_independent`` and ``joint_prob_corr`` collapse to
    the same value -- the simple product. Everything else (gate
    check, EV calc, output schema) is identical to the baseline so
    the leaderboard scoring / reliability tables stay apples-to-
    apples.
    """
    if not _combo_is_compatible(combo):
        return None

    joint = float(np.prod([l.side_probability for l in combo]))
    combined_dec = float(np.prod([l.decimal_odds for l in combo]))
    implied = 1.0 / combined_dec
    fair_dec = 1.0 / joint if joint > 0 else float("inf")
    ev = expected_value_units(joint, combined_dec, config.default_stake_units)

    candidate = ParlayCandidate(
        legs=tuple(combo),
        joint_prob_independent=joint,
        joint_prob_corr=joint,
        fair_decimal_odds=fair_dec,
        combined_decimal_odds=combined_dec,
        implied_prob=implied,
        ev_units=ev,
        stake_units=config.default_stake_units,
    )
    if not qualify_parlay(combo, joint, ev, config=config):
        return None
    return candidate


class IndependentEngine(ParlayEngine):
    name = "independent"
    description = (
        "Same enumeration as baseline but skips the 10k-trial Gaussian-"
        "copula MC -- joint prob is the simple product of leg probs. "
        "Tests whether the copula's correlation adjustment is worth "
        "its compute cost."
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
        candidates: list[ParlayCandidate] = []
        for n in range(2, config.max_legs + 1):
            for combo in itertools.combinations(pool, n):
                cand = _candidate_under_independence(combo, config=config)
                if cand is not None:
                    candidates.append(cand)
        candidates.sort(key=lambda c: c.ev_units, reverse=True)
        return candidates
