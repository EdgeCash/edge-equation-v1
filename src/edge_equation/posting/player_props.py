"""
Player Prop Projections section.

Free-content rendering only -- appears on the 4pm Spotlight and 11am
Daily Edge cards when any of the slate's A+ / A graded picks are in a
prop market. Separate from the main play blocks so the reader sees
team-level projections and player-level projections at a glance
without either section bloating the other.

Brand rules baked in:
  - Facts Not Feelings. No tout language anywhere in this module.
  - No DFS or app mentions.
  - No "Top N" language (the natural edge bar does the curation).
  - Text-only, pipe-separated table:

        Player Prop Projections -- April 22
        Player | Market | Projected Value | Grade | Key Read
        Aaron Judge | Home Runs | 0.82 | A+ | Barrel rate +5pp ...

  - No units, no edge percentages, no Kelly. This section goes to the
    FREE X feed; premium detail lives in the subscriber email only.
  - Admits picks graded A+ or A (same bar as Daily Edge). If none
    qualify, the section does NOT render -- no forcing content.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence

from edge_equation.engine.pick_schema import Pick


# Every prop market the engine knows about. Synced with EXPECTATION_MARKETS
# and the overseas-exclude list in posting_formatter -- if you add a new
# prop market type, update all three sites.
PROP_MARKETS = frozenset({
    "HR", "K",
    "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
})

# Plural, reader-facing labels for the prop section. Intentionally
# DIFFERENT from play_text.MARKET_LABEL -- that map is for play-line
# context ("LAA @ NYY - Home Run"), this map is for table rows
# ("Aaron Judge | Home Runs | 0.82 | ...").
PROP_MARKET_LABEL = {
    "HR": "Home Runs",
    "K": "Strikeouts",
    "Passing_Yards": "Passing Yards",
    "Rushing_Yards": "Rushing Yards",
    "Receiving_Yards": "Receiving Yards",
    "Points": "Points",
    "Rebounds": "Rebounds",
    "Assists": "Assists",
    "SOG": "Shots on Goal",
}

# Free-content grade floor. Same bar as Daily Edge so the feed has a
# consistent curation standard -- users never see a B-grade prop even
# if it's high-edge-for-its-tier.
_PROP_ALLOWED_GRADES = frozenset({"A+", "A"})

# Default fallback when the engine didn't populate a read note. Must NOT
# read like an apology or hedge -- it's a factual absence statement.
_DEFAULT_READ = "No analytical delta recorded."


@dataclass(frozen=True)
class PropProjectionRow:
    """One row in the Player Prop Projections section."""
    player: str
    market_label: str
    projected_value: str
    grade: str
    key_read: str

    def to_dict(self) -> dict:
        return {
            "player": self.player,
            "market_label": self.market_label,
            "projected_value": self.projected_value,
            "grade": self.grade,
            "key_read": self.key_read,
        }

    def to_text(self) -> str:
        """Pipe-separated rendering matching the brand spec exactly."""
        return (
            f"{self.player} | {self.market_label} | "
            f"{self.projected_value} | {self.grade} | {self.key_read}"
        )


# ------------------------------------------------------- helpers

def _player_name(pick: Pick) -> str:
    """Extract the player name from a pick. Prefers a metadata key if
    the engine set one; otherwise strips the common "over/under/yes/no"
    suffix from the selection string."""
    meta = pick.metadata or {}
    explicit = meta.get("player_name") or meta.get("player")
    if explicit:
        return str(explicit).strip()
    selection = (pick.selection or "").strip()
    if not selection:
        return "Unknown"
    # Split on first occurrence of the over/under/yes/no markers.
    lowered = selection.lower()
    for marker in (" over ", " under ", " yes ", " no "):
        idx = lowered.find(marker)
        if idx != -1:
            return selection[:idx].strip() or selection
    return selection


def _projected_value(pick: Pick) -> str:
    """Render the engine's projected value for this prop. Prefers
    pick.expected_value (present for rate props like HR / K / yards);
    falls back to fair_prob for binary prop markets (which we don't
    currently have but keep the fallback safe)."""
    ev = pick.expected_value
    if ev is not None:
        q = Decimal("0.01")
        return str(Decimal(str(ev)).quantize(q))
    fp = pick.fair_prob
    if fp is not None:
        q = Decimal("0.001")
        return str(Decimal(str(fp)).quantize(q))
    return "--"


def _key_read(pick: Pick) -> str:
    meta = pick.metadata or {}
    read = (meta.get("read_notes") or meta.get("read") or "").strip()
    return read or _DEFAULT_READ


def _row_for(pick: Pick) -> PropProjectionRow:
    market_label = PROP_MARKET_LABEL.get(pick.market_type, pick.market_type)
    return PropProjectionRow(
        player=_player_name(pick),
        market_label=market_label,
        projected_value=_projected_value(pick),
        grade=pick.grade or "?",
        key_read=_key_read(pick),
    )


# Premium Player Prop Projections table caps at 10 rows by design --
# subscribers want a curated "best of" for the day, sorted by grade
# then edge. Free content keeps the uncapped selector to match the
# spec's "no top-N language" constraint for the public feed.
PREMIUM_TOP_N_PROPS = 10
# Premium admits A+/A/A- (engine grade B renders as the brand's "A-").
_PREMIUM_PROP_GRADES = frozenset({"A+", "A", "B"})


def select_top_props_by_grade(
    picks: Sequence[Pick],
    n: int = PREMIUM_TOP_N_PROPS,
) -> List[Pick]:
    """Premium prop-section selector: top N props ranked strictly by
    grade (A+ > A > A-), then by edge descending. Includes A- where
    the free selector does not, because premium subscribers see the
    full analytical bar down to Grade B edge >= 3%."""
    eligible = [
        p for p in picks
        if p.market_type in PROP_MARKETS
        and (p.grade or "") in _PREMIUM_PROP_GRADES
    ]
    grade_rank = {"A+": 2, "A": 1, "B": 0}
    eligible.sort(
        key=lambda p: (
            grade_rank.get(p.grade or "", -1),
            Decimal(str(p.edge)) if p.edge is not None else Decimal("0"),
        ),
        reverse=True,
    )
    return eligible[:n]


# ------------------------------------------------------- public API

def select_prop_projections(picks: Sequence[Pick]) -> List[Pick]:
    """Filter down to A+/A graded prop-market picks. Sorts by grade
    (A+ first) then by expected_value descending so the reader sees
    the strongest projection at the top. No cap applied -- the edge
    bar itself is the curator."""
    eligible = [
        p for p in picks
        if p.market_type in PROP_MARKETS
        and (p.grade or "") in _PROP_ALLOWED_GRADES
    ]
    grade_order = {"A+": 1, "A": 0}
    eligible.sort(
        key=lambda p: (
            grade_order.get(p.grade, 0),
            Decimal(str(p.expected_value)) if p.expected_value is not None else Decimal("0"),
        ),
        reverse=True,
    )
    return eligible


def build_prop_rows(picks: Sequence[Pick]) -> List[PropProjectionRow]:
    """Convert selected picks into pipe-separated row dataclasses."""
    return [_row_for(p) for p in select_prop_projections(picks)]


def render_prop_section(
    picks: Sequence[Pick],
    date_str: Optional[str] = None,
) -> str:
    """
    Render the full pipe-separated prop section text. Returns an
    empty string if no picks qualify -- the caller SHOULD skip
    emitting the section entirely in that case rather than showing
    a header with no rows.

    date_str is the human-readable date for the section header.
    Callers typically pass the card's generated_at.date() isoformat.
    """
    rows = build_prop_rows(picks)
    if not rows:
        return ""
    date_label = _date_header(date_str)
    lines: List[str] = []
    lines.append(f"Player Prop Projections -- {date_label}")
    lines.append("Player | Market | Projected Value | Grade | Key Read")
    for row in rows:
        lines.append(row.to_text())
    return "\n".join(lines)


def _date_header(raw: Optional[str]) -> str:
    """Friendly 'April 22' header from a 'YYYY-MM-DD...' input. Falls
    back to the raw string when parsing fails."""
    if not raw:
        return "Today"
    date_part = raw.split("T", 1)[0]
    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_part)
        return d.strftime("%B %-d")
    except Exception:
        return raw
