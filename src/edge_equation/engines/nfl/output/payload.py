"""NFL canonical output payload — placeholder.

Mirrors `FullGameOutput` from the MLB full-game engine; the field
list will diverge slightly to carry NFL-specific audit data
(rest_bucket, qb_status, weather_impact_score). The factory + email
+ API adapters land in Phase F-2.

Phase F-1 ships only the dataclass shell so other modules can import
the type without tripping on missing-symbol errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class NFLOutput:
    """Skeleton — fields will mirror `FullGameOutput` plus NFL-only
    audit columns. See `engines/full_game/output/payload.py` for the
    target layout."""

    # Identity
    event_id: str = ""
    market_type: str = ""           # 'Spread' / 'Total' / 'ML' / player-prop key
    market_label: str = ""
    home_team: str = ""
    away_team: str = ""
    home_tricode: str = ""
    away_tricode: str = ""
    side: str = ""
    line_value: Optional[float] = None

    # Probability (filled in F-2 once projection module ships)
    model_prob: float = 0.0
    model_pct: float = 0.0
    market_prob: float = 0.0
    market_prob_raw: float = 0.0
    vig_corrected: bool = False

    # Color
    color_band: str = "Orange"
    color_hex: str = "#ef6c00"

    # Drivers
    driver_text: list[str] = field(default_factory=list)

    # NFL-specific audit columns (planned)
    rest_bucket_home: str = "unknown"
    rest_bucket_away: str = "unknown"
    qb_status_home: str = "HEALTHY"
    qb_status_away: str = "HEALTHY"
    weather_impact: float = 0.0

    # Market & stake
    edge_pp: float = 0.0
    kelly_units: Optional[float] = None
    american_odds: float = -110.0
    decimal_odds: float = 1.91
    book: str = ""

    # Tier classification
    tier: str = "NO_PLAY"

    # Audit trail
    grade: str = "F"
    engine: str = "nfl_skeleton"
    model_version: str = "nfl_v0_skeleton"
