"""Parlay candidate builder + correlation-adjusted joint probability.

Pipeline
--------

1. Caller supplies a pool of `ParlayLeg` objects — single-leg picks
   that already cleared their market's tier classifier.
2. Builder filters to legs that meet `config.min_tier` (STRONG
   default per the audit) and discards mutually-exclusive pairs
   (e.g., NRFI + YRFI on the same game).
3. Generate every leg combination of size 2..max_legs.
4. For each combination, compute:
   - Independence joint prob (product of leg probabilities).
   - Correlation-adjusted joint prob via Gaussian copula MC.
   - Combined decimal odds (product of leg decimal odds).
   - Implied prob from combined odds.
   - Expected value at the configured stake.
5. Keep the candidates that pass the joint-prob and EV gates.
6. Rank by EV (descending) so the operator's eye lands on the best
   ticket first.

Why a Gaussian copula?
    It's the standard textbook approach for combining marginals with
    a correlation matrix. Implementation is just numpy matmuls + a
    Cholesky factor of the (clipped) correlation matrix. For a 2-3
    leg parlay with N=10,000 trials this runs in ~1ms on a laptop.

The MC isn't load-bearing for correctness — when the correlation
table reduces to an identity matrix the MC estimate matches the
independence product to within Monte Carlo error (validated by the
test suite). The MC's value is making the same-game / same-player
correlation explicit, so a parlay that *looks* like 4 independent
70%-shots (joint = 0.24) honestly reflects its real ~0.32 hit rate
when the legs are correlated.
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

from edge_equation.engines.tiering import Tier
from edge_equation.utils.kelly import american_to_decimal

from .config import ParlayConfig


log = logging.getLogger(__name__)
from .correlations import (
    ParlayLegContext, are_mutually_exclusive, correlation_for_pair,
)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParlayLeg:
    """A single leg in a parlay candidate."""
    market_type: str           # 'NRFI' / 'ML' / 'Total' / 'HR' / ...
    side: str                  # 'Under 0.5', 'Yankees ML', 'Over 8.5', ...
    side_probability: float    # calibrated model prob for this side, 0..1
    american_odds: float       # market price for this side
    tier: Tier                 # gating tier from classify_tier()
    game_id: Optional[str] = None
    player_id: Optional[str] = None
    label: str = ""            # display string for the email / dashboard

    @property
    def context(self) -> ParlayLegContext:
        return ParlayLegContext(
            market_type=self.market_type, side=self.side,
            game_id=self.game_id, player_id=self.player_id,
        )

    @property
    def decimal_odds(self) -> float:
        return american_to_decimal(self.american_odds)


@dataclass(frozen=True)
class ParlayCandidate:
    """A qualified parlay ready to be displayed / recorded."""
    legs: tuple[ParlayLeg, ...]
    joint_prob_independent: float
    joint_prob_corr: float        # correlation-adjusted via MC
    fair_decimal_odds: float      # 1 / joint_prob_corr
    combined_decimal_odds: float  # product of leg.decimal_odds
    implied_prob: float           # 1 / combined_decimal_odds
    ev_units: float               # expected return at stake_units (signed)
    stake_units: float

    @property
    def n_legs(self) -> int:
        return len(self.legs)

    @property
    def combined_american_odds(self) -> float:
        return _decimal_to_american(self.combined_decimal_odds)

    @property
    def edge_pp(self) -> float:
        """Edge over implied (vigged) book probability, in percentage points."""
        return (self.joint_prob_corr - self.implied_prob) * 100.0


# ---------------------------------------------------------------------------
# Money math
# ---------------------------------------------------------------------------


def _decimal_to_american(decimal_odds: float) -> float:
    """Inverse of `american_to_decimal`."""
    if decimal_odds <= 1.0:
        return 0.0
    if decimal_odds >= 2.0:
        return (decimal_odds - 1.0) * 100.0
    return -100.0 / (decimal_odds - 1.0)


def expected_value_units(
    joint_prob: float, combined_decimal_odds: float, stake_units: float,
) -> float:
    """Signed EV in units. Positive = net win expectation."""
    win_payout = (combined_decimal_odds - 1.0) * stake_units
    loss = -1.0 * stake_units
    return joint_prob * win_payout + (1.0 - joint_prob) * loss


# ---------------------------------------------------------------------------
# Correlation-adjusted MC joint probability
# ---------------------------------------------------------------------------


def _build_correlation_matrix(
    legs: Sequence[ParlayLeg], *, max_abs_correlation: float,
) -> np.ndarray:
    """Pairwise ρ matrix for `legs`, clipped to [-cap, +cap].

    Diagonal is 1; off-diagonal from `correlation_for_pair`. The clip
    keeps the matrix safely positive semi-definite for Cholesky even
    when the table has near-±1 entries.
    """
    n = len(legs)
    mat = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            rho = correlation_for_pair(legs[i].context, legs[j].context)
            rho = max(-max_abs_correlation, min(max_abs_correlation, rho))
            mat[i, j] = rho
            mat[j, i] = rho
    return mat


def simulate_correlated_joint_prob(
    legs: Sequence[ParlayLeg],
    *,
    n_trials: int = 10_000,
    seed: int = 42,
    max_abs_correlation: float = 0.85,
) -> float:
    """Estimate P(all legs hit) via Gaussian-copula Monte Carlo.

    Each trial draws a vector of correlated standard normals z ~ N(0, Σ),
    converts to uniforms via Φ, and counts a leg as hitting when its
    uniform is ≤ leg's marginal probability. The fraction of trials
    where every leg hits is the joint probability estimate.

    For 1-leg "parlays" (degenerate case) the legs's own probability
    is returned directly so callers don't have to special-case it.
    """
    if not legs:
        return 0.0
    probs = np.array([float(l.side_probability) for l in legs])
    # Clamp to avoid numerical blow-up when an upstream prob is 0/1.
    probs = np.clip(probs, 1e-6, 1.0 - 1e-6)
    n = len(legs)
    if n == 1:
        return float(probs[0])

    # Correlation matrix Σ; nudge with tiny diagonal noise if the
    # Cholesky still complains about numerical PSD.
    sigma = _build_correlation_matrix(
        legs, max_abs_correlation=max_abs_correlation,
    )
    try:
        L = np.linalg.cholesky(sigma)
    except np.linalg.LinAlgError:
        sigma = sigma + 1e-6 * np.eye(n)
        L = np.linalg.cholesky(sigma)

    rng = np.random.default_rng(seed)
    eta = rng.standard_normal(size=(n_trials, n))
    z = eta @ L.T          # (n_trials, n) correlated standard normals
    # Φ-CDF of standard normal — closed form via erf.
    u = 0.5 * (1.0 + _vec_erf(z / math.sqrt(2.0)))
    hits = u <= probs[None, :]
    all_hit = hits.all(axis=1)
    return float(all_hit.mean())


def _vec_erf(x: np.ndarray) -> np.ndarray:
    """Vectorised math.erf — numpy doesn't ship one, so use the
    standard Abramowitz & Stegun polynomial approximation. ~1e-7
    absolute error, well below MC sampling noise at N=10k."""
    # Constants from A&S 7.1.26.
    a1, a2, a3 = 0.254829592, -0.284496736, 1.421413741
    a4, a5     = -1.453152027, 1.061405429
    p = 0.3275911
    sign = np.sign(x)
    abs_x = np.abs(x)
    t = 1.0 / (1.0 + p * abs_x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t \
        * np.exp(-abs_x * abs_x)
    return sign * y


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _tier_rank(tier: Tier) -> int:
    """ELITE > STRONG > MODERATE > LEAN > NO_PLAY."""
    return {Tier.ELITE: 4, Tier.STRONG: 3, Tier.MODERATE: 2,
            Tier.LEAN: 1, Tier.NO_PLAY: 0}[tier]


def _passes_min_tier(leg: ParlayLeg, min_tier: Tier) -> bool:
    return _tier_rank(leg.tier) >= _tier_rank(min_tier)


def _combo_is_compatible(combo: Sequence[ParlayLeg]) -> bool:
    """Reject combos that contain a mutually-exclusive pair
    (e.g., NRFI + YRFI on the same game).

    Also rejects combos with two ML legs on the same game (you'd be
    betting both teams to win the same game)."""
    for i, leg_a in enumerate(combo):
        for leg_b in combo[i + 1:]:
            if are_mutually_exclusive(leg_a.context, leg_b.context):
                return False
            # Same-game same-market doesn't make sense for ML / Run_Line
            # (you'd be betting both sides). Allow same-market across
            # different games (parlaying NRFI in two separate games).
            if (leg_a.game_id is not None
                and leg_a.game_id == leg_b.game_id
                and leg_a.market_type == leg_b.market_type
                and leg_a.side != leg_b.side):
                return False
    return True


def qualify_parlay(
    legs: Sequence[ParlayLeg],
    joint_prob_corr: float,
    ev_units: float,
    *,
    config: ParlayConfig,
) -> bool:
    """All-or-nothing gate: every check must pass."""
    if len(legs) < 2:
        return False
    if len(legs) > config.max_legs:
        return False
    if any(not _passes_min_tier(l, config.min_tier) for l in legs):
        return False
    if joint_prob_corr < config.min_joint_prob:
        return False
    if ev_units < config.min_ev_units:
        return False
    return True


def _candidate_for_combo(
    combo: Sequence[ParlayLeg], *, config: ParlayConfig,
) -> Optional[ParlayCandidate]:
    """Compute the joint prob, combined odds, and EV for one combo.
    Returns None when the combo doesn't qualify."""
    if not _combo_is_compatible(combo):
        return None

    # Independence baseline (sanity reference for the MC).
    joint_indep = float(np.prod([l.side_probability for l in combo]))

    # Correlation-adjusted joint via MC.
    joint_corr = simulate_correlated_joint_prob(
        combo,
        n_trials=config.mc_trials,
        seed=config.mc_seed,
        max_abs_correlation=config.max_abs_correlation,
    )

    combined_dec = float(np.prod([l.decimal_odds for l in combo]))
    implied = 1.0 / combined_dec
    fair_dec = 1.0 / joint_corr if joint_corr > 0 else float("inf")
    ev = expected_value_units(
        joint_corr, combined_dec, config.default_stake_units,
    )

    candidate = ParlayCandidate(
        legs=tuple(combo),
        joint_prob_independent=joint_indep,
        joint_prob_corr=joint_corr,
        fair_decimal_odds=fair_dec,
        combined_decimal_odds=combined_dec,
        implied_prob=implied,
        ev_units=ev,
        stake_units=config.default_stake_units,
    )
    if not qualify_parlay(combo, joint_corr, ev, config=config):
        return None
    return candidate


def build_parlay_candidates(
    legs: Iterable[ParlayLeg],
    *,
    config: Optional[ParlayConfig] = None,
) -> list[ParlayCandidate]:
    """Generate every qualifying parlay candidate from `legs`.

    Sorted by EV descending so the daily report shows the strongest
    Special Drop first. Returns an empty list when no combo qualifies
    — typical for the audit-strict thresholds + a normal-day slate.

    When the qualifying pool exceeds ``config.max_pool_size``, the top
    legs by single-leg EV are kept and the rest dropped before
    enumeration. This caps the combinatorial sweep at C(max_pool_size,
    max_legs) and keeps Daily Master inside its workflow timeout on
    high-volume slates (props produced 62 LEAN+ legs on 2026-05-07,
    which made C(62, 2..6) ~= 68M combos × 10k MC trials = trillions
    of ops, hanging the workflow).
    """
    cfg = config or ParlayConfig()
    pool = [l for l in legs if _passes_min_tier(l, cfg.min_tier)]
    if len(pool) > cfg.max_pool_size:
        n_before = len(pool)
        # Sort by single-leg EV per unit risked = decimal_odds * p - 1
        # so the legs the builder enumerates over are the strongest
        # bets in the pool. Tier breakers are already implicit in
        # side_probability (higher prob -> better leg).
        pool.sort(
            key=lambda l: l.decimal_odds * l.side_probability,
            reverse=True,
        )
        pool = pool[: cfg.max_pool_size]
        log.info(
            "parlay builder: pool capped %d -> %d (top by single-leg EV) "
            "to keep enumeration tractable.",
            n_before, cfg.max_pool_size,
        )
    candidates: list[ParlayCandidate] = []

    for n in range(2, cfg.max_legs + 1):
        for combo in itertools.combinations(pool, n):
            cand = _candidate_for_combo(combo, config=cfg)
            if cand is not None:
                candidates.append(cand)

    candidates.sort(key=lambda c: c.ev_units, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def render_candidate(candidate: ParlayCandidate) -> str:
    """Plain-text rendering of a single candidate for the daily email
    / dashboard. Shows each leg + tier + the joint-prob + EV reasoning."""
    lines = [
        f"PARLAY ({candidate.n_legs} legs)  "
        f"@ {candidate.combined_decimal_odds:.2f}x "
        f"({_format_american(candidate.combined_american_odds)})",
        "─" * 60,
    ]
    for i, leg in enumerate(candidate.legs, 1):
        prob_pct = leg.side_probability * 100.0
        odds = _format_american(leg.american_odds)
        label = leg.label or f"{leg.market_type} {leg.side}"
        lines.append(
            f"  {i}. [{leg.tier.value:<8}] {label}  "
            f"{prob_pct:5.1f}%  {odds}"
        )
    lines.append("─" * 60)
    lines.append(
        f"  joint prob (indep)   {candidate.joint_prob_independent*100:5.1f}%"
    )
    lines.append(
        f"  joint prob (corr)    {candidate.joint_prob_corr*100:5.1f}%"
    )
    lines.append(
        f"  implied (book)       {candidate.implied_prob*100:5.1f}%"
    )
    lines.append(
        f"  edge                 {candidate.edge_pp:+.1f}pp"
    )
    lines.append(
        f"  EV @ {candidate.stake_units:.2f}u stake     "
        f"{candidate.ev_units:+.3f}u"
    )
    return "\n".join(lines)


def _format_american(odds: float) -> str:
    if odds == 0:
        return "n/a"
    return f"{odds:+.0f}" if odds > 0 else f"{odds:.0f}"
