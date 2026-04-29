"""Full-game market constants — canonical names + Odds API mapping.

The engine's canonical market names match `posting.posting_formatter`'s
existing vocabulary (ML / Run_Line / Total) so the table renderer
doesn't need a translation layer. The Odds API uses different keys
which we map here once.

Markets supported in the FG-1 ship:
* ML        — moneyline   (h2h)
* Run_Line  — run line    (spreads)         — typically -1.5 / +1.5
* Total     — game total  (totals)          — e.g. Over 8.5
* F5_Total  — 1st-5-inn   (totals_1st_5_innings)
* F5_ML     — 1st-5-inn   (h2h_1st_5_innings)
* Team_Total — per-team   (alternate_team_totals) — premium markets
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class FullGameMarket:
    """One MLB full-game market this engine projects on."""
    canonical: str            # 'ML' / 'Run_Line' / 'Total' / 'F5_Total' / 'F5_ML' / 'Team_Total'
    label: str                # operator-facing label
    odds_api_key: str         # Odds API market key
    side_kind: str            # 'team' | 'over_under'
    requires_alternate: bool = False  # True for premium-tier-only markets


MLB_FULL_GAME_MARKETS: dict[str, FullGameMarket] = {
    "ML": FullGameMarket(
        canonical="ML", label="Moneyline",
        odds_api_key="h2h", side_kind="team",
    ),
    "Run_Line": FullGameMarket(
        canonical="Run_Line", label="Run Line",
        odds_api_key="spreads", side_kind="team",
    ),
    "Total": FullGameMarket(
        canonical="Total", label="Total Runs",
        odds_api_key="totals", side_kind="over_under",
    ),
    "F5_Total": FullGameMarket(
        canonical="F5_Total", label="F5 Total Runs",
        odds_api_key="totals_1st_5_innings",
        side_kind="over_under",
        requires_alternate=True,
    ),
    "F5_ML": FullGameMarket(
        canonical="F5_ML", label="F5 Moneyline",
        odds_api_key="h2h_1st_5_innings", side_kind="team",
        requires_alternate=True,
    ),
    "Team_Total": FullGameMarket(
        canonical="Team_Total", label="Team Total Runs",
        odds_api_key="alternate_team_totals", side_kind="over_under",
        requires_alternate=True,
    ),
}


# Standard markets only (free-tier OK). Used by `fetch_event_list` so we
# don't burn alt-market credits when we just need event_ids + team names.
STANDARD_MARKETS_PARAM: str = ",".join(
    m.odds_api_key for m in MLB_FULL_GAME_MARKETS.values()
    if not m.requires_alternate
)

# All markets including alternates — for the per-event endpoint.
ALL_MARKETS_PARAM: str = ",".join(
    m.odds_api_key for m in MLB_FULL_GAME_MARKETS.values()
)


def all_markets() -> Sequence[FullGameMarket]:
    """Return every FG market the engine knows about, in canonical order."""
    return tuple(MLB_FULL_GAME_MARKETS.values())


def market_for_odds_api_key(key: str) -> FullGameMarket | None:
    """Reverse lookup: Odds API key → canonical FullGameMarket."""
    for m in MLB_FULL_GAME_MARKETS.values():
        if m.odds_api_key == key:
            return m
    return None
