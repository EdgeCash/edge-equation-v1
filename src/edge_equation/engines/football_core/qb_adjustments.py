"""Quarterback injury / status → expected-points adjustment.

A single position dominates football scoring projection in a way no
MLB position does. The QB1 → QB2 swap typically moves the spread
4-7 points. Late-week injury status changes (Wednesday "Limited"
practice → Sunday "Out") are one of the highest-signal pieces of
information for the football engine.

Skeleton: enum + lookup table. The real magnitudes need to be
calibrated per-team since not every QB downgrade is equal — Patrick
Mahomes → backup is a 9-point swing; an average starter → solid
backup is closer to 3 points. The projection layer feeds a
per-team override into ``expected_points_delta_for`` once we have
team-level QB depth charts and historical backup performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class QBStatus(str, Enum):
    """NFL injury report taxonomy (mirrors official designations)."""
    HEALTHY = "HEALTHY"
    PROBABLE = "PROBABLE"      # legacy term, still surfaces in NCAAF reports
    QUESTIONABLE = "QUESTIONABLE"
    DOUBTFUL = "DOUBTFUL"
    OUT = "OUT"
    INJURED_RESERVE = "IR"


@dataclass(frozen=True)
class QBAdjustment:
    """Expected-points delta + flag for downstream rendering.

    `delta` is the points-delta to apply to the team's expected
    score relative to the QB1-healthy baseline. Negative for any
    downgrade; positive only when a backup is rated above the
    listed starter (rare but happens, e.g. mid-season takeover).
    """
    status: QBStatus
    delta: float                 # points; negative for downgrades
    confidence: float = 0.5      # 0..1; how reliable the status read is
    note: str = ""


# League-average defaults — applied when we don't have a per-team
# depth-chart calibration yet. Real per-team multipliers come from a
# follow-up PR with historical backup-performance regression.
DEFAULT_DELTA_BY_STATUS: dict[QBStatus, float] = {
    QBStatus.HEALTHY:         0.0,
    QBStatus.PROBABLE:        0.0,
    QBStatus.QUESTIONABLE:    -1.0,   # 50/50 the starter goes
    QBStatus.DOUBTFUL:        -3.0,
    QBStatus.OUT:             -5.0,
    QBStatus.INJURED_RESERVE: -5.5,
}


def expected_points_delta_for(
    status: QBStatus, *,
    team_specific_delta: Optional[float] = None,
) -> QBAdjustment:
    """Return the expected-points adjustment for a QB status.

    `team_specific_delta` overrides the league default when supplied
    (e.g. Mahomes-out → -9.0, sub-replacement starter → 0.0).
    """
    delta = (
        team_specific_delta if team_specific_delta is not None
        else DEFAULT_DELTA_BY_STATUS.get(status, 0.0)
    )
    confidence = (
        0.9 if status in (QBStatus.HEALTHY, QBStatus.OUT,
                              QBStatus.INJURED_RESERVE) else 0.5
    )
    return QBAdjustment(
        status=status, delta=float(delta), confidence=confidence,
    )
