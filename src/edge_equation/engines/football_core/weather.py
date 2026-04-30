"""Outdoor-venue weather classification for football engines.

Football weather impact (skeleton — research notes for the projection
layer to consume):

* **Wind ≥ 15 mph** — totals drop ~0.5 points; passing yards drop
  ~3-5%. Wind ≥ 20 mph drops totals by ~1 full point.
* **Temperature ≤ 32 °F** — totals drop ~0.3 points; rushing yards
  per attempt rise slightly (more dive/run plays).
* **Precipitation (rain or snow)** — totals drop ~0.5 points;
  fumble rate ticks up; passing yards fall further than rushing.
* **Dome / retractable closed** — neutral. Treat as still air.

The projection layer pulls a single ``VenueWeatherProfile`` per game
from this module and folds the per-feature impact into the team /
player rate adjustments. The shared layer just classifies venues —
implementations of the impact functions belong in NFL / NCAAF
projection modules where the per-game scoring environment is
calibrated.

Status: skeleton only. The full venue dictionary needs to be
populated from MLB Stats API equivalents for football — likely the
NFLverse `nflfastR` venue table for NFL and the `cfbfastR` venue
table for NCAAF. Both are publicly available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class VenueWeatherProfile:
    """Weather treatment for a single venue."""
    venue_code: str
    venue_name: str
    is_dome: bool
    is_retractable: bool
    typical_wind_mph: float = 0.0  # average October wind speed at the venue
    altitude_ft: int = 0           # Denver / Salt Lake City matter for kicking
    region: str = ""               # 'NE' / 'SE' / 'MW' / 'W' / 'SW' / 'NW'


def is_outdoor(profile: VenueWeatherProfile, *, retractable_open: bool = True) -> bool:
    """True when the game is played in actual weather conditions.

    Retractable-roof venues (AT&T Stadium, NRG Stadium, U.S. Bank
    Stadium, State Farm Stadium, Allegiant Stadium) default to
    "open" since most regular-season games are. Pre-game roof status
    can be overridden when known.
    """
    if profile.is_dome:
        return False
    if profile.is_retractable and not retractable_open:
        return False
    return True


def weather_impact_score(
    *,
    wind_mph: float,
    temperature_f: float,
    precipitation_prob: float,
) -> float:
    """Return a [-1.0, 0.0] multiplier on game total — placeholder.

    The projection layer multiplies expected total points by
    ``(1.0 + weather_impact_score(...))`` so a 0.0 score means no
    weather effect and -0.05 means "totals drop ~5%".

    Skeleton implementation — calibrated impact coefficients land in
    a follow-up PR once we have a backtest harness. Conservative
    placeholder logic:

    * Wind ≥ 20 mph → -0.06
    * Wind ≥ 15 mph → -0.03
    * Temp ≤ 25 °F → -0.04
    * Temp ≤ 32 °F → -0.02
    * Precip prob ≥ 60 % → -0.04
    * Precip prob ≥ 30 % → -0.02

    Effects compound (capped at -0.10 floor).
    """
    score = 0.0
    if wind_mph >= 20.0:
        score -= 0.06
    elif wind_mph >= 15.0:
        score -= 0.03

    if temperature_f <= 25.0:
        score -= 0.04
    elif temperature_f <= 32.0:
        score -= 0.02

    if precipitation_prob >= 0.60:
        score -= 0.04
    elif precipitation_prob >= 0.30:
        score -= 0.02

    return max(-0.10, score)
