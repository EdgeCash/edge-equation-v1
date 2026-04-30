"""NCAAF market vocabulary + Odds API mapping.

Wraps the shared `football_core.markets.SHARED_FOOTBALL_MARKETS` with
NCAAF-specific Odds API keys + the college-football-only player-prop
inventory (fewer markets posted than NFL — books drop player props
on the smaller-conference matchups).

The Odds API uses ``sport_key="americanfootball_ncaaf"``.
"""

from __future__ import annotations

from ..football_core.markets import (
    FootballMarket, PROP_MARKET_LABELS, SHARED_FOOTBALL_MARKETS,
)


SPORT_KEY = "americanfootball_ncaaf"


# Reuse shared games-level markets. ML can be missing on big
# blowouts (lines of -30+) since books take the moneyline off the
# board; the orchestrator handles missing markets gracefully.
NCAAF_MARKETS: dict[str, FootballMarket] = dict(SHARED_FOOTBALL_MARKETS)


# Player-prop market keys — narrower than NFL since books only post
# props for the major-conference / ranked-matchup games.
NCAAF_PROP_ODDS_API_KEYS: dict[str, str] = {
    "player_pass_yds":       "Pass_Yds",
    "player_pass_tds":       "Pass_TDs",
    "player_rush_yds":       "Rush_Yds",
    "player_rush_attempts":  "Rush_Att",
    "player_reception_yds":  "Rec_Yds",
    "player_receptions":     "Rec_Recs",
    "player_anytime_td":     "Anytime_TD",
}


__all__ = [
    "SPORT_KEY",
    "NCAAF_MARKETS",
    "NCAAF_PROP_ODDS_API_KEYS",
    "FootballMarket",
    "PROP_MARKET_LABELS",
]
