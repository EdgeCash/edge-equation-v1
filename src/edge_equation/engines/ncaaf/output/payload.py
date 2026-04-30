"""NCAAF canonical output payload — placeholder.

Mirrors `NFLOutput` plus college-specific audit columns
(conference_tier, recruit_rating_delta, transfer_portal_flag).
The factory + email + API adapters land in Phase F-2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class NCAAFOutput:
    """Skeleton — fields will mirror `NFLOutput` plus NCAAF-only
    audit columns. See the NFL payload for the target layout."""

    # Identity
    event_id: str = ""
    market_type: str = ""
    market_label: str = ""
    home_team: str = ""
    away_team: str = ""
    home_tricode: str = ""
    away_tricode: str = ""
    side: str = ""
    line_value: Optional[float] = None

    # Probability
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

    # NCAAF-specific audit columns (planned)
    conference_home: str = ""
    conference_away: str = ""
    conference_tier_home: str = "Unknown"   # 'P5' / 'G5' / 'FCS' / 'Unknown'
    conference_tier_away: str = "Unknown"
    recruit_rating_delta: float = 0.0       # composite-ratings home-minus-away
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
    engine: str = "ncaaf_skeleton"
    model_version: str = "ncaaf_v0_skeleton"
