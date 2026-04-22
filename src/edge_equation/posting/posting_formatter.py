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
    # --- Premium (subscriber email only; never posted to X)
    "premium_daily": {
        "headline": "Premium Daily Edge",
        "subhead": "Full analytical read: every A+ / A / A- game, parlay of the day, top 6 DFS props, and yesterday's engine hit rate.",
    },
}


# Market types excluded from Overseas Edge (props never ship on overseas
# cards per Phase 20 brand rule).
_OVERSEAS_EXCLUDED_MARKETS = frozenset({
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
})

_OVERSEAS_ALLOWED_SPORTS = frozenset({"KBO", "NPB", "Soccer"})

# Premium Daily grade admission. Brand-intent "A-" maps to the engine's
# Grade B tier (edge 0.03-0.05); the premium email surfaces it while
# the free Daily Edge stays A+/A only.
_PREMIUM_DAILY_ALLOWED_GRADES = frozenset({"A+", "A", "B"})

# Prop markets surfaced in the premium DFS top-6 block. (These are the
# same markets the Overseas Edge filter excludes from free content.)
_PROP_MARKETS = frozenset({
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
})

# Parlay-of-the-day: pick the N highest-trend picks that come from
# distinct games so no two legs are correlated.
_PREMIUM_PARLAY_SIZE = 3


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
    def filter_premium_daily(picks: Sequence[Pick]) -> List[Pick]:
        """Premium email includes every A+ / A / A- pick, no cap. Sorted
        grade-first (A+ highest) then by edge descending so the reader
        sees the strongest plays at the top."""
        qualifying = [p for p in picks if p.grade in _PREMIUM_DAILY_ALLOWED_GRADES]
        order = {"A+": 2, "A": 1, "B": 0}
        qualifying.sort(
            key=lambda p: (
                order.get(p.grade, 0),
                p.edge if p.edge is not None else Decimal("0"),
            ),
            reverse=True,
        )
        return qualifying

    @staticmethod
    def select_parlay_of_day(
        picks: Sequence[Pick],
        n: int = _PREMIUM_PARLAY_SIZE,
    ) -> List[Pick]:
        """Pick N legs from distinct games, ranked by grade * edge. Does
        NOT raise when fewer than N are available -- returns the best
        feasible set so the premium email still renders a parlay block
        on thin-slate days."""
        from edge_equation.posting.spotlight import _pick_contribution
        ranked = sorted(picks, key=_pick_contribution, reverse=True)
        legs: List[Pick] = []
        seen_games: set = set()
        for p in ranked:
            gid = p.game_id or ""
            if not gid or gid in seen_games:
                continue
            seen_games.add(gid)
            legs.append(p)
            if len(legs) >= n:
                break
        return legs

    @staticmethod
    def select_top_props(picks: Sequence[Pick], n: int = 6) -> List[Pick]:
        """Surface the N highest-edge prop-market picks for the DFS
        subscriber section. Props are the markets Overseas Edge excludes
        from free content. Picks with non-positive edge are dropped
        (no-edge props are noise for DFS selection)."""
        props = [
            p for p in picks
            if p.market_type in _PROP_MARKETS
            and p.edge is not None
            and p.edge > Decimal("0")
        ]
        props.sort(key=lambda p: p.edge, reverse=True)
        return props[:n]

    @staticmethod
    def evening_edge_is_stable(
        current_picks: Sequence[Pick],
        prior_picks: Optional[Sequence[Pick]] = None,
    ) -> bool:
        """
        Phase 20: Evening Edge posts only when something material shifted
        since the prior slate. "Material" means any of:
          - the set of (game_id, market_type, selection) rows changed
          - the grade on any identical row changed
          - the priced line moved (American odds or the point number)
          - an injury / rest flag surfaced in the pick metadata

        Returns True iff NONE of those changed -> the card short-circuits
        to the "engine stable" note. A missing baseline (prior_picks is
        None) is always treated as a material update so the first-ever
        evening run always posts.
        """
        if prior_picks is None:
            return False

        def _key(p: Pick):
            line = p.line
            odds = int(line.odds) if line is not None and line.odds is not None else None
            number = str(line.number) if line is not None and line.number is not None else None
            meta = p.metadata or {}
            injury_flag = bool(meta.get("injury")) or bool(meta.get("injury_flag"))
            rest_flag = bool(meta.get("rest_flag")) or bool(meta.get("bullpen_fatigue"))
            return (
                p.game_id or "",
                p.market_type or "",
                p.selection or "",
                p.grade or "C",
                odds,
                number,
                injury_flag,
                rest_flag,
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
        engine_health: Optional[dict] = None,
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
        # Snapshot the UNFILTERED input so the Player Prop Projections
        # section (daily_edge + spotlight only) can surface prop-market
        # picks that wouldn't otherwise survive the Top-5 or trending-
        # game filters. This keeps team plays and player projections
        # as parallel sections rather than competing for the same slots.
        unfiltered_picks_for_props: List[Pick] = list(picks_list)
        template = CARD_TEMPLATES[card_type]
        subhead_final = subhead_override or template["subhead"]
        parlay_legs: List[Pick] = []
        top_props: List[Pick] = []

        # --- Phase 20 card-specific filtering
        if card_type == "daily_edge" and not skip_filter:
            picks_list = PostingFormatter.filter_daily_edge(picks_list)
        elif card_type == "overseas_edge" and not skip_filter:
            picks_list = PostingFormatter.filter_overseas(picks_list)
        elif card_type == "spotlight" and not skip_filter:
            from edge_equation.posting.spotlight import select_spotlight_game
            selection = select_spotlight_game(picks_list)
            picks_list = list(selection.picks)
            if not picks_list:
                subhead_final = "No single game crossed the Spotlight bar today."
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
        elif card_type == "premium_daily" and not skip_filter:
            full_pool = list(picks_list)
            picks_list = PostingFormatter.filter_premium_daily(full_pool)
            # Parlay + top props are selected from the FULL premium pool,
            # not the already-sorted picks_list, so a high-edge prop that
            # didn't sort to the top still surfaces in the DFS block.
            parlay_legs = PostingFormatter.select_parlay_of_day(picks_list)
            top_props = PostingFormatter.select_top_props(full_pool)

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

        if card_type == "premium_daily":
            card["parlay"] = [p.to_dict() for p in parlay_legs]
            card["top_props"] = [p.to_dict() for p in top_props]
            if engine_health is not None:
                card["engine_health"] = dict(engine_health)

        # Player Prop Projections section for the 4pm Spotlight and 11am
        # Daily Edge public cards. Falls out to an empty section (and
        # therefore renders nothing) when no prop pick clears the A+/A
        # bar -- no forcing content.
        if card_type in ("daily_edge", "spotlight") and not skip_filter:
            from edge_equation.posting.player_props import (
                render_prop_section, select_prop_projections,
            )
            prop_picks = select_prop_projections(unfiltered_picks_for_props)
            if prop_picks:
                card["player_prop_projections"] = {
                    "picks": [p.to_dict() for p in prop_picks],
                    "text": render_prop_section(prop_picks, date_str=generated_at),
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
