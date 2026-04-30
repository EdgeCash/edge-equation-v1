"""Canonical football market vocabulary — shared NFL + NCAAF.

Both leagues post the same core market set on US books (DraftKings,
FanDuel, BetMGM, Caesars, etc.):

* **Spread** — point spread (e.g. NYG -3.5). The dominant football
  market; tighter than ML on both efficiency and volume.
* **Total** — full-game O/U on combined points. Outdoor weather +
  pace-of-play features drive the projection.
* **Moneyline** — straight winner. Less prominent than baseball;
  for big NFL favorites the ML can't be priced sensibly so books
  often take it off the board.
* **Alternate spreads / totals** — buy-the-hook variations (e.g.
  -3.5 → -2.5 at lower juice). Future PR will surface these as
  separate markets so the engine can identify which buy/sell point
  is the operator's best edge.
* **Player props** — passing yards, rushing yards, receiving yards,
  anytime TD, longest reception, etc. Distinct from MLB props
  vocabulary (no HR / Hits / K) but the engine pattern is identical:
  per-player rolling rate × game-script-adjusted volume.

Skeleton — concrete Odds API key mappings land in each sport's
``markets.py`` so NFL / NCAAF can diverge if a book drops a market
in one league and not the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class FootballMarket:
    """One football market with operator-facing label + canonical key."""
    canonical: str            # 'Spread' / 'Total' / 'ML' / 'Pass_Yds' / ...
    label: str                # 'Spread' / 'Total Points' / ...
    side_kind: str            # 'team' | 'over_under' | 'player_over_under' | 'yes_no'
    requires_alternate: bool = False  # True for alt-spread / alt-total markets


SHARED_FOOTBALL_MARKETS: dict[str, FootballMarket] = {
    "Spread": FootballMarket(
        canonical="Spread", label="Spread",
        side_kind="team",
    ),
    "Total": FootballMarket(
        canonical="Total", label="Total Points",
        side_kind="over_under",
    ),
    "ML": FootballMarket(
        canonical="ML", label="Moneyline",
        side_kind="team",
    ),
    "Alternate_Spread": FootballMarket(
        canonical="Alternate_Spread", label="Alt Spread",
        side_kind="team", requires_alternate=True,
    ),
    "Alternate_Total": FootballMarket(
        canonical="Alternate_Total", label="Alt Total",
        side_kind="over_under", requires_alternate=True,
    ),
}


# Player-prop labels (operator-facing). Each sport's `markets.py` will
# wire the `odds_api_key` per league since the NFL / NCAAF prop
# inventories overlap but aren't identical.
PROP_MARKET_LABELS: dict[str, str] = {
    "Pass_Yds":   "Passing Yards",
    "Pass_TDs":   "Passing TDs",
    "Pass_Att":   "Passing Attempts",
    "Pass_Comp":  "Passing Completions",
    "Pass_Ints":  "Interceptions Thrown",
    "Rush_Yds":   "Rushing Yards",
    "Rush_Att":   "Rushing Attempts",
    "Rush_TDs":   "Rushing TDs",
    "Rec_Yds":    "Receiving Yards",
    "Rec_Recs":   "Receptions",
    "Rec_TDs":    "Receiving TDs",
    "Anytime_TD": "Anytime TD",
    "Longest_Rec": "Longest Reception",
    "Longest_Rush": "Longest Rush",
}


def all_markets() -> Sequence[FootballMarket]:
    """Return every shared football market, in canonical order."""
    return tuple(SHARED_FOOTBALL_MARKETS.values())
