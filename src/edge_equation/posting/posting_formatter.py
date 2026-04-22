"""
Posting formatter.

Facts Not Feelings. Text-only cards. No graphics, no network. Pure dict
output.

Phase 20 adds:
  - Three new card types: the_ledger, spotlight, multi_leg_projection.
  - Daily Edge top-5 A/A+ filter: only Grade A or A+ picks survive, and
    we cap the list at 5. If fewer than 5 qualify, post fewer. No
    forcing content.
  - Overseas Edge scope: KBO / NPB / Soccer only, NO props.
  - Evening Edge "engine stable" short-form when a prior slate exists
    and nothing material has shifted.
  - Mandatory Season Ledger footer injection on every public-mode card.
  - Forbidden-term scrubbing for hype language that slipped into pick
    metadata (the compliance checker is the final gate; this is a soft
    sanitizer at render time).

Hashtag rules (Phase 20): cards DO NOT carry hashtags themselves. The
publisher's tweet renderer is the only surface that appends hashtags,
capped at two per post -- only #FactsNotFeelings and/or #EdgeEquation.
"""
from decimal import Decimal
from typing import Iterable, List, Optional, Sequence

from edge_equation.compliance.disclaimer import (
    DISCLAIMER_TEXT,
    inject_into_card,
)
from edge_equation.compliance.sanitizer import PublicModeSanitizer
from edge_equation.engine.pick_schema import Pick
from edge_equation.posting.ledger import LedgerStats, format_ledger_footer


TAGLINE = "Facts. Not Feelings."


CARD_TEMPLATES = {
    # --- Phase 20 five-window daily cadence
    "the_ledger": {
        "headline": "The Ledger",
        "subhead": "Yesterday's results. Season record. Model health.",
    },
    "daily_edge": {
        "headline": "Daily Edge",
        "subhead": "Today's Grade A and A+ projections. Data speaks for itself.",
    },
    "spotlight": {
        "headline": "Spotlight",
        "subhead": "Deep analytical dive on today's most-trending matchup.",
    },
    "evening_edge": {
        "headline": "Evening Edge",
        "subhead": "Late-slate rerun. Post only when the engine moved.",
    },
    "overseas_edge": {
        "headline": "Overseas Edge",
        "subhead": "International slate -- KBO, NPB, and global soccer.",
    },
    # --- Supplemental (not part of the daily mandatory cadence but still
    # supported for premium / ad-hoc runs)
    "highlighted_game": {
        "headline": "Highlighted Game",
        "subhead": "Tonight's model focus.",
    },
    "model_highlight": {
        "headline": "Model Highlight",
        "subhead": "Top-graded projection from the engine. Hype-free.",
    },
    "sharp_signal": {   # internal label; the Free/X renderer must NOT publish this card type
        "headline": "Model Signal",
        "subhead": "Internal model divergence from consensus.",
    },
    "the_outlier": {
        "headline": "The Outlier",
        "subhead": "Where the model and the market most disagree.",
    },
    "multi_leg_projection": {
        "headline": "Multi-Leg Projection",
        "subhead": "Rare multi-game analytical chain. 3 to 6 legs, posted only when the edge is real.",
    },
}


# Market types excluded from Overseas Edge (props never ship on overseas
# cards per Phase 20 brand rule).
_OVERSEAS_EXCLUDED_MARKETS = frozenset({
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
})

_OVERSEAS_ALLOWED_SPORTS = frozenset({"KBO", "NPB", "Soccer"})


# Daily Edge cap and grade filter per Phase 20.
DAILY_EDGE_TOP_N = 5
_DAILY_EDGE_ALLOWED_GRADES = frozenset({"A+", "A"})


# Multi-Leg Projection leg bounds per Phase 20.
MULTI_LEG_MIN = 3
MULTI_LEG_MAX = 6


class PostingFormatter:

    # ------------------------------------------------ helpers

    @staticmethod
    def _best_grade(picks: list) -> str:
        if not picks:
            return "C"
        order = {"A+": 5, "A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
        return max(picks, key=lambda p: order.get(p.grade, 0)).grade

    @staticmethod
    def _max_edge(picks: list):
        edges = [p.edge for p in picks if p.edge is not None]
        return max(edges) if edges else None

    @staticmethod
    def _max_kelly(picks: list):
        kellys = [p.kelly for p in picks if p.kelly is not None]
        return max(kellys) if kellys else None

    # ------------------------------------------------ Phase 20 filters

    @staticmethod
    def filter_daily_edge(picks: Sequence[Pick]) -> List[Pick]:
        """
        Top-5 Grade A / A+ filter. Sort by grade (A+ first) then by edge
        descending to break ties. Never more than DAILY_EDGE_TOP_N.
        """
        qualifying = [p for p in picks if p.grade in _DAILY_EDGE_ALLOWED_GRADES]
        order = {"A+": 1, "A": 0}
        qualifying.sort(
            key=lambda p: (
                order.get(p.grade, 0),
                p.edge if p.edge is not None else Decimal('0'),
            ),
            reverse=True,
        )
        return qualifying[:DAILY_EDGE_TOP_N]

    @staticmethod
    def filter_overseas(picks: Sequence[Pick]) -> List[Pick]:
        """KBO / NPB / Soccer only; drop every prop market."""
        return [
            p for p in picks
            if p.sport in _OVERSEAS_ALLOWED_SPORTS
            and p.market_type not in _OVERSEAS_EXCLUDED_MARKETS
        ]

    @staticmethod
    def evening_edge_is_stable(
        current_picks: Sequence[Pick],
        prior_picks: Optional[Sequence[Pick]] = None,
    ) -> bool:
        """
        Phase 20: Evening Edge posts only when something material shifted
        since the prior slate. If the set of (game_id, market_type,
        selection, grade) tuples is unchanged, skip and emit the short
        stable-state note instead.
        """
        if prior_picks is None:
            return False  # no baseline -> treat as a material update
        def _key(p):
            return (
                p.game_id or "",
                p.market_type or "",
                p.selection or "",
                p.grade or "C",
            )
        current = {_key(p) for p in current_picks}
        prior = {_key(p) for p in prior_picks}
        return current == prior

    # ------------------------------------------------ build_card

    @staticmethod
    def build_card(
        card_type: str,
        picks: Iterable[Pick],
        generated_at: Optional[str] = None,
        headline_override: Optional[str] = None,
        subhead_override: Optional[str] = None,
        public_mode: bool = False,
        ledger_stats: Optional[LedgerStats] = None,
        prior_picks: Optional[Sequence[Pick]] = None,
        skip_filter: bool = False,
    ) -> dict:
        """
        Build a card payload. Behavior by card_type:
          - daily_edge:         auto-filter to top 5 A/A+ unless
                                skip_filter=True.
          - overseas_edge:      filter to KBO/NPB/Soccer, no props
                                (skip_filter=True bypasses).
          - evening_edge:       if prior_picks is provided and the set
                                matches current, emits an "engine stable"
                                short card (picks=[], subhead reflects
                                it). Always runs; skip_filter has no
                                effect here.
          - multi_leg_projection: validates 3..6 legs; raises otherwise.
          - other types:        pass-through.

        When public_mode=True:
          - PublicModeSanitizer strips edge / kelly / kelly_breakdown.
          - DISCLAIMER_TEXT appended to tagline (idempotent).
          - ledger_stats (if supplied) rendered as the mandatory footer
            line and appended under the disclaimer.

        skip_filter is an escape hatch for tests and for administrative
        reruns where the caller has pre-curated the picks list. Production
        posting paths must leave it False so the Phase 20 rules apply.
        """
        if card_type not in CARD_TEMPLATES:
            raise ValueError(
                f"Unknown card_type: {card_type}. "
                f"Valid: {sorted(CARD_TEMPLATES.keys())}"
            )
        picks_list = list(picks)
        template = CARD_TEMPLATES[card_type]
        subhead_final = subhead_override or template["subhead"]

        # --- Phase 20 card-specific filtering
        if card_type == "daily_edge" and not skip_filter:
            picks_list = PostingFormatter.filter_daily_edge(picks_list)
        elif card_type == "overseas_edge" and not skip_filter:
            picks_list = PostingFormatter.filter_overseas(picks_list)
        elif card_type == "evening_edge":
            if PostingFormatter.evening_edge_is_stable(picks_list, prior_picks):
                picks_list = []
                subhead_final = "Engine stable -- no material updates."
        elif card_type == "multi_leg_projection":
            if not (MULTI_LEG_MIN <= len(picks_list) <= MULTI_LEG_MAX):
                raise ValueError(
                    f"multi_leg_projection requires {MULTI_LEG_MIN}-"
                    f"{MULTI_LEG_MAX} legs, got {len(picks_list)}"
                )

        summary = {
            "grade": PostingFormatter._best_grade(picks_list),
            "edge": PostingFormatter._max_edge(picks_list),
            "kelly": PostingFormatter._max_kelly(picks_list),
        }
        summary["edge"] = str(summary["edge"]) if summary["edge"] is not None else None
        summary["kelly"] = str(summary["kelly"]) if summary["kelly"] is not None else None

        card = {
            "card_type": card_type,
            "headline": headline_override or template["headline"],
            "subhead": subhead_final,
            "picks": [p.to_dict() for p in picks_list],
            "summary": summary,
            "tagline": TAGLINE,
            "generated_at": generated_at,
        }

        if public_mode:
            card = PublicModeSanitizer.sanitize_card(card)
            card = inject_into_card(card, disclaimer=DISCLAIMER_TEXT)
            if ledger_stats is not None:
                footer = format_ledger_footer(ledger_stats)
                existing = card.get("tagline") or ""
                if footer not in existing:
                    card["tagline"] = (
                        f"{existing}\n{footer}" if existing else footer
                    )

        return card
