"""Production parlay-construction strategies.

The MLB parlay engines (game-results + player-props) used to call
``build_parlay_candidates`` directly --- a fixed
itertools+copula-MC enumeration. The parlay_lab shootout (PRs #186,
#187, #188) proved that **different problem shapes favor different
algorithms**:

  - Independent legs (game-results, moneyline / run_line / totals):
    ILP wins ROI by 22pp over the baseline.
  - Correlated legs (player-props, same-player stacking):
    Same-player de-dup wins ROI by ~10pp.

This module exposes those algorithms as plain functions with the
same shape as ``build_parlay_candidates`` so the production parlay
engines can swap between them via env-var flags. The
``parlay_lab/engines/`` ParlayEngine subclasses wrap these same
functions, keeping the shootout and production on a single source
of truth.

Naming: each strategy is named for the parlay-construction approach,
not the leg pool. Pick by structure of the legs you're combining.
"""

from __future__ import annotations

import itertools
import logging
import math
from typing import Callable, Optional, Sequence

from .builder import (
    ParlayCandidate,
    ParlayLeg,
    _candidate_for_combo,
    _passes_min_tier,
    build_parlay_candidates,
)
from .config import ParlayConfig


log = logging.getLogger(__name__)


# Single function signature every strategy implements. Engines depend
# only on this contract --- no inheritance.
Strategy = Callable[[list[ParlayLeg], ParlayConfig], list[ParlayCandidate]]


# ---------------------------------------------------------------------------
# Baseline --- the existing ``build_parlay_candidates``.
# ---------------------------------------------------------------------------


def build_baseline(
    legs: list[ParlayLeg], config: ParlayConfig,
) -> list[ParlayCandidate]:
    """Reference strategy: itertools enumeration + Gaussian-copula MC.

    Identical to ``build_parlay_candidates`` --- this re-export exists
    so callers can hold a ``Strategy`` reference and switch via flag
    without an extra import path.
    """
    return build_parlay_candidates(legs, config=config)


# ---------------------------------------------------------------------------
# Deduped --- best for correlated leg pools (player props).
# ---------------------------------------------------------------------------


def _single_leg_ev(leg: ParlayLeg) -> float:
    """Expected return per 1u risked = decimal_odds * p - 1.

    Used as the de-dup tiebreak --- when two legs sit in the same
    correlation group (same player, else same game), the higher
    single-leg EV survives.
    """
    return leg.decimal_odds * leg.side_probability - 1.0


def _dedup_key(leg: ParlayLeg) -> str:
    """Group identifier: ``player:<id>`` when set, else ``game:<id>``,
    else empty (each ungrouped leg passes through)."""
    if leg.player_id:
        return f"player:{leg.player_id}"
    if leg.game_id:
        return f"game:{leg.game_id}"
    return ""


def build_deduped(
    legs: list[ParlayLeg], config: ParlayConfig,
) -> list[ParlayCandidate]:
    """Same-player / same-game de-dup before enumeration.

    Groups qualifying legs by ``player_id`` (else ``game_id``), keeps
    the highest single-leg-EV leg per group, and then runs the standard
    builder on the deduped pool. Tested by the shootout's prop
    backfill where it wins ROI by ~10pp over baseline thanks to
    same-player correlation breaking.
    """
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


# ---------------------------------------------------------------------------
# ILP --- best for independent leg pools (game-results).
# ---------------------------------------------------------------------------


_DEFAULT_MAX_PER_GAME: int = 1


try:
    import pulp  # type: ignore
    _PULP_OK = True
except ImportError:
    pulp = None  # type: ignore
    _PULP_OK = False


def _ilp_solve_one_size(
    pool: list[ParlayLeg], *, k: int, max_per_game: int = _DEFAULT_MAX_PER_GAME,
) -> Optional[list[ParlayLeg]]:
    """Pick the EV-optimal k-leg subset by maximizing sum of log(p*d)
    under same-game / same-player caps.

    The objective is "log of joint EV under independence" --- a tractable
    linear surrogate for the true non-linear parlay EV. The picked
    subset is re-evaluated through the standard
    ``_candidate_for_combo`` after solving so the public
    ``joint_prob_corr`` reflects the real Gaussian copula.
    """
    if not _PULP_OK or pulp is None:
        return None
    if len(pool) < k:
        return None
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
    prob += pulp.lpSum(x) == k

    by_game: dict[str, list[int]] = {}
    for i, leg in enumerate(pool):
        if leg.game_id:
            by_game.setdefault(leg.game_id, []).append(i)
    for _gid, idxs in by_game.items():
        if len(idxs) > max_per_game:
            prob += pulp.lpSum(x[i] for i in idxs) <= max_per_game

    by_player: dict[str, list[int]] = {}
    for i, leg in enumerate(pool):
        if leg.player_id:
            by_player.setdefault(leg.player_id, []).append(i)
    for _pid, idxs in by_player.items():
        if len(idxs) > 1:
            prob += pulp.lpSum(x[i] for i in idxs) <= 1

    solver = pulp.PULP_CBC_CMD(msg=False)
    status = prob.solve(solver)
    if status != pulp.LpStatusOptimal:
        return None
    chosen = [
        pool[i] for i in range(len(pool))
        if x[i].varValue and x[i].varValue > 0.5
    ]
    return chosen if len(chosen) == k else None


def build_ilp(
    legs: list[ParlayLeg], config: ParlayConfig,
) -> list[ParlayCandidate]:
    """ILP-optimal subset per parlay size, picked by max sum-of-log(p*d).

    Falls back gracefully to the baseline when PuLP isn't installed
    --- production stays on its feet, the operator just sees a
    one-line warning. Install via ``pip install -e '.[parlay-lab]'``
    to enable.

    For each size in [min_legs(2), max_legs], runs one CBC solve under
    same-game (<=1) + same-player (<=1) constraints and re-evaluates
    the picked subset through the standard copula MC. Replaces
    enumeration entirely, so the parlay-pool cap from PR #184 is
    unnecessary when this strategy is active.
    """
    if not _PULP_OK:
        log.warning(
            "parlay strategy=ilp: PuLP not installed, falling back to "
            "baseline. Install via `pip install -e '.[parlay-lab]'`.",
        )
        return build_baseline(legs, config)

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
        chosen = _ilp_solve_one_size(pool, k=k)
        if not chosen:
            continue
        cand = _candidate_for_combo(tuple(chosen), config=config)
        if cand is not None:
            candidates.append(cand)
    candidates.sort(key=lambda c: c.ev_units, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Registry + lookup
# ---------------------------------------------------------------------------


_STRATEGIES: dict[str, Strategy] = {
    "baseline": build_baseline,
    "deduped": build_deduped,
    "ilp": build_ilp,
}


def get_strategy(name: str) -> Strategy:
    """Map a string name to the strategy function. Unknown names fall
    through to baseline with a warning rather than raising --- a typo
    in a flag should not crash the daily card."""
    fn = _STRATEGIES.get(name.lower().strip())
    if fn is None:
        log.warning(
            "parlay strategy=%r unknown, falling back to baseline. "
            "Known strategies: %s",
            name, ", ".join(sorted(_STRATEGIES)),
        )
        return build_baseline
    return fn


def known_strategies() -> list[str]:
    """Stable, sorted list of strategy names for help text + tests."""
    return sorted(_STRATEGIES)
