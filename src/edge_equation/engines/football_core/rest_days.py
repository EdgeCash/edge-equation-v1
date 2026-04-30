"""Rest-day classifier for football engines.

NFL teams play once per week with significant variance:
* **Short rest (3-4 days)** — Thursday Night Football after a Sunday
  game. Historically depresses team performance ~1.5-2 points.
* **Standard rest (6-7 days)** — Sunday-to-Sunday. Baseline.
* **Long rest (10+ days)** — bye-week game, or coming off Monday
  Night Football into a Sunday. Historically lifts performance
  modestly (~0.5-1.0 point).

NCAAF is mostly Saturday-to-Saturday. Outliers:
* **Friday night games** — short rest for one or both teams.
* **Tuesday/Wednesday MAC weeknight games** — standalone exposure.
* **Bowl prep** — extended rest (3-6 weeks). Calibrated separately.

The projection layer uses ``classify_rest`` to bucket teams into
one of four categories and applies a per-bucket multiplicative
adjustment to expected scoring.

Status: skeleton. Real impact coefficients land after we have a
backtest with at least 4 years of game-script data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass(frozen=True)
class RestProfile:
    """Days-since-last-game + the engine's bucket label."""
    team: str
    days_since_last_game: int
    bucket: str  # 'short' | 'standard' | 'long' | 'bye' | 'unknown'


def classify_rest(
    team: str, *,
    last_game_date: Optional[date] = None,
    this_game_date: date,
    is_bye_week: bool = False,
) -> RestProfile:
    """Classify a team's rest profile for a single matchup.

    `is_bye_week=True` overrides the day count and forces the
    'bye' bucket (typically 13-14 days off).
    """
    if is_bye_week:
        return RestProfile(team=team, days_since_last_game=14, bucket="bye")
    if last_game_date is None:
        return RestProfile(team=team, days_since_last_game=-1,
                              bucket="unknown")
    days = (this_game_date - last_game_date).days
    if days <= 4:
        bucket = "short"
    elif days <= 8:
        bucket = "standard"
    else:
        bucket = "long"
    return RestProfile(team=team, days_since_last_game=days, bucket=bucket)


# Per-bucket expected-points-delta lookup. Values are placeholder
# educated guesses; real values come from a fitted model on backtest
# data. The projection layer reads this to nudge the team's expected
# score before computing the spread / total / ML probability.
REST_POINT_DELTA: dict[str, float] = {
    "short":    -1.5,   # Thursday-after-Sunday penalty
    "standard": 0.0,    # baseline
    "long":     +0.5,   # post-bye / extra prep
    "bye":      +0.8,   # full bye-week recovery + scheme prep
    "unknown":  0.0,    # missing data → no adjustment
}


def expected_points_delta_for_rest(bucket: str) -> float:
    """Return the bucket's points-delta to apply to team expected score."""
    return REST_POINT_DELTA.get(bucket, 0.0)
