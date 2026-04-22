"""
Phase 20 posting cadence.

Five hard-coded windows per day, all in US/Central time (CT):

    09:00 CT  ->  The Ledger       yesterday's results + full Season Ledger + model health note
    11:00 CT  ->  Daily Edge       flagship -- top 5 A/A+ only, no forcing
    16:00 CT  ->  Spotlight        deep analytical dive on most-trending game
    18:00 CT  ->  Evening Edge     rerun engine; "engine stable" if no material changes
    23:00 CT  ->  Overseas Edge    KBO / NPB / Soccer only, no props

Stored as pure data so the scheduler layer (GitHub Actions / Vercel Cron /
systemd timer) can translate CT-hour to UTC-cron without duplication.

DST-sensitive: the Central timezone shifts +/- an hour through the year.
Callers translate to UTC per run via zoneinfo (stdlib) when needed.
"""
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


CARD_TYPE_LEDGER = "the_ledger"
CARD_TYPE_DAILY_EDGE = "daily_edge"
CARD_TYPE_SPOTLIGHT = "spotlight"
CARD_TYPE_EVENING_EDGE = "evening_edge"
CARD_TYPE_OVERSEAS_EDGE = "overseas_edge"

# Phase 20 also supports the earlier card types -- we keep them around
# for premium use or administrative reruns, but only the five above are
# part of the mandatory daily cadence.
SUPPLEMENTAL_CARD_TYPES = (
    "highlighted_game",
    "model_highlight",
    "sharp_signal",         # internal label only; never rendered to X
    "the_outlier",
    "multi_leg_projection",
)


CENTRAL_TZ = "America/Chicago"


@dataclass(frozen=True)
class CadenceSlot:
    """One mandatory daily post window."""
    card_type: str
    hour_ct: int            # 0..23 local Central time
    minute_ct: int = 0
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "card_type": self.card_type,
            "hour_ct": self.hour_ct,
            "minute_ct": self.minute_ct,
            "description": self.description,
        }


# The five mandatory daily windows. Order matters: cadence runs left-to-right
# through the day.
CADENCE_WINDOWS: Tuple[CadenceSlot, ...] = (
    CadenceSlot(
        card_type=CARD_TYPE_LEDGER,
        hour_ct=9, minute_ct=0,
        description="Yesterday's results + full Season Ledger + model health note.",
    ),
    CadenceSlot(
        card_type=CARD_TYPE_DAILY_EDGE,
        hour_ct=11, minute_ct=0,
        description="Flagship: top 5 A / A+ projections only. Post only what qualifies.",
    ),
    CadenceSlot(
        card_type=CARD_TYPE_SPOTLIGHT,
        hour_ct=16, minute_ct=0,
        description="Deep analytical dive on the most-trending game. Pure deltas, no forced projection.",
    ),
    CadenceSlot(
        card_type=CARD_TYPE_EVENING_EDGE,
        hour_ct=18, minute_ct=0,
        description="Evening rerun. Post only if meaningful changes; short 'engine stable' note otherwise.",
    ),
    CadenceSlot(
        card_type=CARD_TYPE_OVERSEAS_EDGE,
        hour_ct=23, minute_ct=0,
        description="KBO / NPB / Soccer only. No props.",
    ),
)


CADENCE_BY_CARD_TYPE: Dict[str, CadenceSlot] = {
    s.card_type: s for s in CADENCE_WINDOWS
}


def slot_for(card_type: str) -> Optional[CadenceSlot]:
    return CADENCE_BY_CARD_TYPE.get(card_type)


def is_mandatory(card_type: str) -> bool:
    """True iff this card type is in the five daily mandatory windows."""
    return card_type in CADENCE_BY_CARD_TYPE
