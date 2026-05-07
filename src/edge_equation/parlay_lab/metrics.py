"""Per-engine performance scoring.

Given an engine's recommended candidates for each historical slate,
grade them against the slate's actuals and compute the leaderboard
metrics. The "winner" is the engine with the best calibrated ROI ---
positive ROI alone can be lucky on a small sample, but a calibrated
engine with positive ROI is the real signal.

Push policy: a parlay leg with ``result == 'PUSH'`` is treated as
removed (the parlay collapses to a smaller one). This matches every
US sportsbook's standard handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Iterable, Optional

from edge_equation.engines.parlay.builder import ParlayCandidate

from .base import GradedLeg, GradedSlate


# Default stake per parlay (units). Matches ``ParlayConfig.default_stake_units``.
_DEFAULT_STAKE: float = 0.5


@dataclass(frozen=True)
class ParlayOutcome:
    """One recommended parlay's actual P/L."""
    date: str
    n_legs: int
    n_legs_active: int        # after dropping pushes
    decimal_payout: float     # product of leg odds for active legs
    units_pl: float           # +stake * (payout - 1) on win, -stake on loss
    result: str               # 'WIN' / 'LOSS' / 'PUSH' / 'VOID'
    joint_prob_corr: float    # what the engine claimed before grading


def grade_parlay(
    candidate: ParlayCandidate,
    slate: GradedSlate,
    *,
    stake_units: float = _DEFAULT_STAKE,
) -> Optional[ParlayOutcome]:
    """Grade one candidate against a slate's actuals.

    Returns ``None`` when any leg can't be matched in the slate
    (defensive: an engine that emits a leg the slate didn't supply
    is buggy and we'd rather notice than silently grade as loss).
    """
    active_odds: list[float] = []
    n_legs_active = 0
    n_pushes = 0
    n_losses = 0
    for leg in candidate.legs:
        graded = slate.lookup(leg)
        if graded is None:
            return None
        if graded.result == "PUSH":
            n_pushes += 1
            continue
        if graded.result == "LOSS":
            n_losses += 1
            continue
        if graded.result == "WIN":
            active_odds.append(graded.decimal_odds)
            n_legs_active += 1
            continue
        # Unknown grade --- treat as void so we don't lie about PnL.
        return None

    if n_losses > 0:
        return ParlayOutcome(
            date=slate.date,
            n_legs=len(candidate.legs),
            n_legs_active=n_legs_active,
            decimal_payout=0.0,
            units_pl=-stake_units,
            result="LOSS",
            joint_prob_corr=candidate.joint_prob_corr,
        )

    if n_legs_active == 0:
        # Every leg pushed --- stake refunded.
        return ParlayOutcome(
            date=slate.date,
            n_legs=len(candidate.legs),
            n_legs_active=0,
            decimal_payout=1.0,
            units_pl=0.0,
            result="PUSH",
            joint_prob_corr=candidate.joint_prob_corr,
        )

    # All active legs won.
    payout = 1.0
    for d in active_odds:
        payout *= d
    return ParlayOutcome(
        date=slate.date,
        n_legs=len(candidate.legs),
        n_legs_active=n_legs_active,
        decimal_payout=payout,
        units_pl=stake_units * (payout - 1.0),
        result="WIN",
        joint_prob_corr=candidate.joint_prob_corr,
    )


@dataclass
class EngineScore:
    """Aggregated leaderboard row for one engine over the backfill window."""
    engine_name: str
    n_parlays: int = 0
    n_wins: int = 0
    n_losses: int = 0
    n_pushes: int = 0
    n_days_total: int = 0
    n_days_active: int = 0          # days with at least 1 recommendation
    total_stake_units: float = 0.0
    total_pl_units: float = 0.0
    avg_legs: float = 0.0
    max_drawdown_units: float = 0.0
    brier_joint: Optional[float] = None  # calibration of joint_prob_corr
    outcomes: list[ParlayOutcome] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        graded = self.n_wins + self.n_losses
        return self.n_wins / graded if graded else 0.0

    @property
    def roi_pct(self) -> float:
        if self.total_stake_units <= 0:
            return 0.0
        return 100.0 * self.total_pl_units / self.total_stake_units


def score_engine(
    name: str,
    by_slate: Iterable[tuple[GradedSlate, list[ParlayCandidate]]],
    *,
    stake_units: float = _DEFAULT_STAKE,
) -> EngineScore:
    """Walk one engine's per-day candidate output, grade each, and
    aggregate the scoring metrics.
    """
    score = EngineScore(engine_name=name)
    cumulative_pl = 0.0
    peak = 0.0
    total_legs = 0
    days_with_any = 0

    for slate, candidates in by_slate:
        score.n_days_total += 1
        if not candidates:
            continue
        days_with_any += 1
        for cand in candidates:
            outcome = grade_parlay(cand, slate, stake_units=stake_units)
            if outcome is None:
                # Buggy engine emission --- skip without polluting numbers.
                continue
            score.outcomes.append(outcome)
            score.n_parlays += 1
            score.total_stake_units += stake_units
            score.total_pl_units += outcome.units_pl
            total_legs += outcome.n_legs
            cumulative_pl = score.total_pl_units
            peak = max(peak, cumulative_pl)
            drawdown = peak - cumulative_pl
            if drawdown > score.max_drawdown_units:
                score.max_drawdown_units = drawdown
            if outcome.result == "WIN":
                score.n_wins += 1
            elif outcome.result == "LOSS":
                score.n_losses += 1
            elif outcome.result == "PUSH":
                score.n_pushes += 1

    score.n_days_active = days_with_any
    if score.n_parlays > 0:
        score.avg_legs = total_legs / score.n_parlays
        score.brier_joint = _brier_joint(score.outcomes)
    return score


def _brier_joint(outcomes: Iterable[ParlayOutcome]) -> Optional[float]:
    """Brier score on the engine's joint_prob_corr vs binary outcome.

    Lower is better. Treats PUSH (rare) as a no-op for calibration ---
    the parlay neither hit nor missed and including it would skew
    the score either way. Returns None when no graded outcomes exist.
    """
    err_sq_sum = 0.0
    n = 0
    for o in outcomes:
        if o.result == "PUSH":
            continue
        actual = 1.0 if o.result == "WIN" else 0.0
        err_sq_sum += (o.joint_prob_corr - actual) ** 2
        n += 1
    if n == 0:
        return None
    return err_sq_sum / n
