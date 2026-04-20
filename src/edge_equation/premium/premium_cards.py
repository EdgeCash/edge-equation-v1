"""
Premium card builders.

Builds structured payloads from a list of PremiumPick. Pure functions
— no network, no state. The tagline is shared with the standard
posting_formatter for consistency.
"""
from typing import Iterable

from edge_equation.premium.premium_pick import PremiumPick
from edge_equation.premium.premium_formatter import format_premium_pick
from edge_equation.posting.posting_formatter import TAGLINE


def _build(card_type: str, headline: str, subhead: str, premium_picks: Iterable[PremiumPick]) -> dict:
    picks_list = list(premium_picks)
    return {
        "card_type": card_type,
        "headline": headline,
        "subhead": subhead,
        "picks": [format_premium_pick(pp) for pp in picks_list],
        "tagline": TAGLINE,
    }


def build_premium_daily_edge_card(premium_picks) -> dict:
    return _build(
        card_type="premium_daily_edge",
        headline="Premium Daily Edge",
        subhead="Full distributions and model notes.",
        premium_picks=premium_picks,
    )


def build_premium_overseas_edge_card(premium_picks) -> dict:
    return _build(
        card_type="premium_overseas_edge",
        headline="Premium Overseas Edge",
        subhead="International slate with full distributions.",
        premium_picks=premium_picks,
    )
