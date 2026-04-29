"""Player-prop market constants — canonical names + The Odds API mapping.

The engine's canonical market names match `posting.player_props.PROP_MARKET_LABEL`
(HR / K / etc.) so the table renderer doesn't need a translation layer.
The Odds API uses different keys (e.g. ``batter_home_runs``, ``pitcher_strikeouts``)
which we map here once.

Phase 4 light — only the high-volume MLB markets are wired. NFL / NBA
extensions follow the same pattern when those engines come online.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class PropMarket:
    """One MLB player-prop market this engine projects on.

    Each row stitches together the operator-facing label, the canonical
    pick `market_type` (already used elsewhere in the codebase), and the
    Odds API event-endpoint market key. Side direction (`over_label` /
    `under_label`) covers the natural Over/Under or Yes/No semantics.
    """
    canonical: str            # 'HR' / 'K' / 'Hits' / 'Total_Bases' / 'RBI'
    label: str                # 'Home Runs' / 'Strikeouts' / ...
    odds_api_key: str         # 'batter_home_runs' etc.
    role: str                 # 'batter' or 'pitcher'
    over_label: str = "Over"
    under_label: str = "Under"


# Canonical → PropMarket lookup. Keys here MUST stay aligned with
# `posting.player_props.PROP_MARKET_LABEL` so the daily email's prop
# table renderer doesn't need a separate translation.
MLB_PROP_MARKETS: dict[str, PropMarket] = {
    "HR": PropMarket(
        canonical="HR", label="Home Runs",
        odds_api_key="batter_home_runs", role="batter",
        over_label="Over", under_label="Under",
    ),
    "Hits": PropMarket(
        canonical="Hits", label="Hits",
        odds_api_key="batter_hits", role="batter",
    ),
    "Total_Bases": PropMarket(
        canonical="Total_Bases", label="Total Bases",
        odds_api_key="batter_total_bases", role="batter",
    ),
    "RBI": PropMarket(
        canonical="RBI", label="RBIs",
        odds_api_key="batter_rbis", role="batter",
    ),
    "K": PropMarket(
        canonical="K", label="Strikeouts",
        odds_api_key="pitcher_strikeouts", role="pitcher",
    ),
}


def all_markets() -> Sequence[PropMarket]:
    """Return every MLB market the engine knows about, in canonical order."""
    return tuple(MLB_PROP_MARKETS.values())


def market_for_odds_api_key(key: str) -> PropMarket | None:
    """Reverse lookup: Odds API key → canonical PropMarket. None when unmapped."""
    for m in MLB_PROP_MARKETS.values():
        if m.odds_api_key == key:
            return m
    return None


# Comma-joined Odds API markets string, for use in the `markets=` query
# param of the events endpoint. Each market here costs ~3 credits per
# event call (regions × markets), so this list is intentionally narrow.
ODDS_API_MARKETS_PARAM: str = ",".join(
    m.odds_api_key for m in MLB_PROP_MARKETS.values()
)
