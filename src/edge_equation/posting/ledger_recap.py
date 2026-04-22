"""
Daily Ledger recap: yesterday's cross-slot summary.

The 9am "The Ledger" post walks every persisted slate from the prior
UTC day (daily_edge, spotlight, evening_edge, overseas_edge) and
reports what was published plus how it resolved. Keeps the free-feed
brand promise -- a public diary of exactly what the model projected
and how it landed.

Not to be confused with the cumulative Season Ledger footer (the
"Season Ledger: W-L-T +U.UU units +R.R ROI" line). That is always
all-time and appears at the bottom of every free thread.

Output shape (public, free content):

    Yesterday's Results -- April 21

    Daily Edge: 5 projections posted
      - NYY over MIA                    A+    (Win)
      - LAD runline -1.5                A     (Loss)
      - BOS total over 8.5              A     (Pending)
      ...
    Spotlight: NYY @ BOS
      - Judge Home Runs                 A+    (Win)
    Evening Edge: no material update
    Overseas Edge: 3 projections posted
      - Doosan over KT                  A     (Win)
      ...

No edge percentages, no Kelly, no units -- those stay premium-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
import sqlite3

from edge_equation.engine.pick_schema import Pick
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.posting.play_text import MARKET_LABEL
from edge_equation.posting.player_props import PROP_MARKET_LABEL


# Only these card types represent PUBLIC, posted projections. The
# premium_daily slate is not included since premium content isn't in
# scope of the free public Ledger post. The_ledger itself is excluded
# -- a self-referential summary would be pointless.
_PUBLIC_SLATE_CARD_TYPES = (
    "daily_edge",
    "spotlight",
    "evening_edge",
    "overseas_edge",
)

# Pretty labels for the card-type section headers.
_CARD_TYPE_LABEL = {
    "daily_edge": "Daily Edge",
    "spotlight": "Spotlight",
    "evening_edge": "Evening Edge",
    "overseas_edge": "Overseas Edge",
}

# Settled-realization codes (mirrors engine/realization.py constants).
_WIN = 100
_LOSS = 0
_PUSH = 50
_VOID = -1

_OUTCOME_LABEL = {
    _WIN: "Win",
    _LOSS: "Loss",
    _PUSH: "Push",
    _VOID: "Void",
}


@dataclass(frozen=True)
class _SlotRecap:
    """Recap of one cadence slot (card_type) from yesterday."""
    card_type: str
    slate_id: Optional[str]
    picks: List[dict]   # raw pick dicts; renderer pulls the few fields it needs

    def to_dict(self) -> dict:
        return {
            "card_type": self.card_type,
            "slate_id": self.slate_id,
            "n_picks": len(self.picks),
        }


def _market_label(market_type: str) -> str:
    """Prefer the "Home Runs" plural label for props, else play_text's
    singular market label, else the raw market_type."""
    if market_type in PROP_MARKET_LABEL:
        return PROP_MARKET_LABEL[market_type]
    return MARKET_LABEL.get(market_type, market_type or "Market")


def _outcome_label(realization: Optional[int]) -> str:
    if realization is None:
        return "Pending"
    return _OUTCOME_LABEL.get(int(realization), "Pending")


def _yesterday_date(run_dt: datetime) -> date:
    """Pull yesterday's date in the same frame of reference the slate
    was persisted (UTC). run_dt is usually the engine's current run
    time; we back up one calendar day."""
    if run_dt.tzinfo is None:
        base = run_dt.replace(tzinfo=timezone.utc)
    else:
        base = run_dt
    return (base - timedelta(days=1)).date()


def _slate_matches_date(slate: SlateRecord, target_day: date) -> bool:
    """SlateRecord.generated_at is an ISO string; parse it defensively."""
    raw = slate.generated_at or ""
    datepart = raw.split("T", 1)[0]
    try:
        d = date.fromisoformat(datepart)
    except ValueError:
        return False
    return d == target_day


def collect_yesterday_recap(
    conn: sqlite3.Connection,
    run_dt: Optional[datetime] = None,
    limit_per_type: int = 40,
) -> Dict[str, _SlotRecap]:
    """Walk each public cadence slate type and assemble a per-type
    recap from whatever was persisted yesterday.

    Returns a dict keyed by card_type with the relevant SlotRecap. If
    no slate exists for a card_type the recap is still present with
    an empty picks list -- the renderer decides how to render each
    empty state.
    """
    run_dt = run_dt or datetime.utcnow()
    target = _yesterday_date(run_dt)
    recap: Dict[str, _SlotRecap] = {}
    for ct in _PUBLIC_SLATE_CARD_TYPES:
        slates = SlateStore.list_by_card_type(conn, card_type=ct, limit=limit_per_type)
        match = next((s for s in slates if _slate_matches_date(s, target)), None)
        if match is None:
            recap[ct] = _SlotRecap(card_type=ct, slate_id=None, picks=[])
            continue
        pick_records = PickStore.list_by_slate(conn, match.slate_id)
        pick_dicts = [p.to_dict() for p in pick_records]
        recap[ct] = _SlotRecap(
            card_type=ct, slate_id=match.slate_id, picks=pick_dicts,
        )
    return recap


# ----------------------------------------------------------- rendering


def _render_slot(ct: str, slot: _SlotRecap) -> List[str]:
    """Render one card_type's section. Empty slates surface as a short,
    honest line rather than being hidden -- the feed reader learns that
    we tried and simply didn't publish on that slot."""
    label = _CARD_TYPE_LABEL.get(ct, ct)
    if not slot.picks:
        # "evening_edge" specifically: absence is the "engine stable"
        # signal rather than a no-post day. Report it that way.
        if ct == "evening_edge":
            return [f"{label}: no material update."]
        return [f"{label}: no projections posted."]

    lines = [f"{label}: {len(slot.picks)} projection" + ("s" if len(slot.picks) != 1 else "") + " posted"]
    for p in slot.picks:
        market = _market_label(p.get("market_type") or "")
        selection = (p.get("selection") or "?").strip()
        grade = p.get("grade") or "?"
        realization = p.get("realization")
        outcome = _outcome_label(realization)
        # Two-column grid with padded selection column for readability.
        left = f"  - {selection}  [{market}]"
        lines.append(f"{left}  {grade}  ({outcome})")
    return lines


def _ledger_date_header(target_day: date) -> str:
    return target_day.strftime("%B %-d")


def format_daily_recap(
    recap: Dict[str, _SlotRecap],
    run_dt: Optional[datetime] = None,
) -> str:
    """Multi-section plain-text body. Renders in the order of the five
    cadence slots (which is also the natural narrative order for a
    reader walking yesterday's activity)."""
    run_dt = run_dt or datetime.utcnow()
    target = _yesterday_date(run_dt)
    out: List[str] = []
    out.append(f"Yesterday's Results -- {_ledger_date_header(target)}")
    out.append("")
    any_posted = False
    for ct in _PUBLIC_SLATE_CARD_TYPES:
        slot = recap.get(ct)
        if slot is None:
            continue
        if slot.picks:
            any_posted = True
        out.extend(_render_slot(ct, slot))
        out.append("")
    if not any_posted:
        out.append(
            "No public projections landed yesterday -- engine was quiet."
        )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def recap_to_public_dict(recap: Dict[str, _SlotRecap]) -> dict:
    """JSON-friendly snapshot for card["daily_recap"]. Text rendering
    lives in format_daily_recap(); this dict is the machine-readable
    sibling that travels with the card dict through publishers."""
    return {
        ct: {
            "slate_id": slot.slate_id,
            "n_picks": len(slot.picks),
            "picks": list(slot.picks),
        }
        for ct, slot in recap.items()
    }
