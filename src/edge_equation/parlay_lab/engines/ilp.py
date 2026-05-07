"""ILP engine -- one binary-program solve per parlay size.

Hypothesis under test: the research agent's #1 recommendation. ILP
should reach the EV-optimal subset given size + correlation
constraints in O(seconds) at any pool size, regardless of how big
the candidate universe gets.

Mechanism: for each size ``k`` in [min_legs, max_legs], solve

    maximize    sum_i x_i * log(p_i * d_i)
    subject to  sum_i x_i == k
                sum over each game_id    of x_i <= 1   (or <=2 if loosened)
                sum over each player_id  of x_i <= 1
                x_i in {0, 1}

The objective is "log of joint EV under independence" -- a tractable
linear surrogate for the true non-linear parlay EV. The picked
subset is then re-evaluated through the standard
``_candidate_for_combo`` (Gaussian copula MC + strict gate) to get
the real ``joint_prob_corr`` and final EV.

Why solve per-size separately: the size constraint becomes equality
which lets us produce a candidate per ``k``. Otherwise the solver
just returns the same single optimum.

Graceful degradation: if PuLP isn't installed (it's an optional
dep under ``[parlay-lab]``), the engine logs once and returns no
candidates -- the rest of the shootout still runs.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from edge_equation.engines.parlay.builder import (
    ParlayCandidate,
    ParlayLeg,
    _candidate_for_combo,
    _passes_min_tier,
)
from edge_equation.engines.parlay.config import ParlayConfig

from ..base import ParlayEngine


log = logging.getLogger(__name__)


# Soft cap on legs sharing a game_id. Two run-line legs in the same
# game stack tightly -- one is enough. Tunable on the engine if a
# specific hypothesis wants to relax it.
_DEFAULT_MAX_PER_GAME: int = 1


try:
    import pulp  # type: ignore
    _PULP_OK = True
except ImportError:
    pulp = None  # type: ignore
    _PULP_OK = False


def _solve_one_size(
    pool: list[ParlayLeg], *, k: int, max_per_game: int = _DEFAULT_MAX_PER_GAME,
) -> Optional[list[ParlayLeg]]:
    """Pick the best k-leg subset by max sum-of-log(p*d).

    Returns None if PuLP isn't installed, the model is infeasible,
    or any leg has p*d <= 0 (would log-overflow). The caller is
    responsible for re-evaluating the picked subset through the
    real builder.
    """
    if not _PULP_OK or pulp is None:
        return None
    if len(pool) < k:
        return None

    # log_ev coefficients. Skip legs with non-positive expected payout
    # (those couldn't be in an optimum anyway -- the log would be
    # undefined).
    coefs: list[float] = []
    for leg in pool:
        ev_factor = leg.decimal_odds * leg.side_probability
        if ev_factor <= 0:
            return None
        coefs.append(math.log(ev_factor))

    prob = pulp.LpProblem(f"parlay_size_{k}", pulp.LpMaximize)
    x = [
        pulp.LpVariable(f"x_{i}", lowBound=0, upBound=1, cat="Binary")
        for i in range(len(pool))
    ]
    prob += pulp.lpSum(coefs[i] * x[i] for i in range(len(pool)))

    # Exactly k legs.
    prob += pulp.lpSum(x) == k

    # Same-game cap.
    by_game: dict[str, list[int]] = {}
    for i, leg in enumerate(pool):
        if leg.game_id:
            by_game.setdefault(leg.game_id, []).append(i)
    for game_id, idxs in by_game.items():
        if len(idxs) > max_per_game:
            prob += pulp.lpSum(x[i] for i in idxs) <= max_per_game

    # Same-player cap (always <= 1).
    by_player: dict[str, list[int]] = {}
    for i, leg in enumerate(pool):
        if leg.player_id:
            by_player.setdefault(leg.player_id, []).append(i)
    for player_id, idxs in by_player.items():
        if len(idxs) > 1:
            prob += pulp.lpSum(x[i] for i in idxs) <= 1

    solver = pulp.PULP_CBC_CMD(msg=False)
    status = prob.solve(solver)
    if status != pulp.LpStatusOptimal:
        return None
    chosen = [pool[i] for i in range(len(pool)) if x[i].varValue and x[i].varValue > 0.5]
    return chosen if len(chosen) == k else None


class ILPEngine(ParlayEngine):
    name = "ilp"
    description = (
        "ILP (CBC) optimum: maximize sum of log(p*d) under exactly-k-"
        "legs + same-game cap + same-player cap, then re-evaluate the "
        "picked subset through the standard copula MC. One candidate "
        "per parlay size; replaces enumeration entirely."
    )

    def build(
        self,
        legs: list[ParlayLeg],
        config: ParlayConfig,
    ) -> list[ParlayCandidate]:
        if not _PULP_OK:
            log.warning(
                "parlay_lab.ilp: PuLP not installed -- engine returns "
                "no candidates. Install via `pip install -e '.[parlay-lab]'`.",
            )
            return []

        pool = [l for l in legs if _passes_min_tier(l, config.min_tier)]
        if len(pool) > config.max_pool_size:
            pool.sort(
                key=lambda l: l.decimal_odds * l.side_probability,
                reverse=True,
            )
            pool = pool[: config.max_pool_size]

        candidates: list[ParlayCandidate] = []
        min_k = max(2, getattr(config, "min_legs", 2) or 2)
        for k in range(min_k, config.max_legs + 1):
            chosen = _solve_one_size(pool, k=k)
            if not chosen:
                continue
            cand = _candidate_for_combo(tuple(chosen), config=config)
            if cand is not None:
                candidates.append(cand)
        candidates.sort(key=lambda c: c.ev_units, reverse=True)
        return candidates
