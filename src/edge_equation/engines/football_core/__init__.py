"""Football-shared utilities — used by both `engines/nfl/` and `engines/ncaaf/`.

Parallels `engines.tiering` (which is sport-agnostic) but holds
football-specific helpers that don't belong in either NFL or NCAAF
alone:

* **Market types** — Spread, Total, Moneyline, Player Props, Alternate
  Lines. Common across both leagues; canonical names live in
  ``markets.py``.
* **Weather** — outdoor venue map, dome / retractable flags. Football
  weather mostly matters when wind > 15 mph or temp < 40 °F drops
  totals; ``weather.py`` is the single place that classifies a venue.
* **Rest days** — Thu / Sun / Mon (NFL) or Sat (NCAAF). Short rest is
  a meaningful projection feature; ``rest_days.py`` computes the
  per-team gap between games.
* **QB adjustments** — single-position dominance means a QB1 → QB2
  swap moves the line 4–7 points. ``qb_adjustments.py`` carries the
  injury-status → expected-points delta lookup.

Why a shared layer rather than copy-paste into each sport?
NFL and NCAAF share enough vocabulary (spread structure, total
structure, weather + rest impact) that diverging implementations
would slowly drift. The shared layer keeps one source of truth for
the football-only concepts both sports need.

Status: skeleton (Phase F-1). Implementations land in follow-up PRs
once we have real schedule data + odds API plumbing.
"""

from .markets import (
    FootballMarket,
    PROP_MARKET_LABELS,
    SHARED_FOOTBALL_MARKETS,
)
from .qb_adjustments import (
    QBAdjustment,
    QBStatus,
    expected_points_delta_for,
)
from .rest_days import (
    RestProfile,
    classify_rest,
)
from .weather import (
    VenueWeatherProfile,
    is_outdoor,
    weather_impact_score,
)

__all__ = [
    "FootballMarket",
    "PROP_MARKET_LABELS",
    "SHARED_FOOTBALL_MARKETS",
    "QBAdjustment",
    "QBStatus",
    "expected_points_delta_for",
    "RestProfile",
    "classify_rest",
    "VenueWeatherProfile",
    "is_outdoor",
    "weather_impact_score",
]
