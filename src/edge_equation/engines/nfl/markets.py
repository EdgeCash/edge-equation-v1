"""NFL market vocabulary + Odds API mapping.

Wraps the shared `football_core.markets.SHARED_FOOTBALL_MARKETS` with
NFL-specific Odds API keys + the NFL-only player-prop set.

The Odds API uses ``sport_key="americanfootball_nfl"``; the per-event
endpoint accepts the alternate-market keys below for player props.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..football_core.markets import (
    FootballMarket, PROP_MARKET_LABELS, SHARED_FOOTBALL_MARKETS,
)


SPORT_KEY = "americanfootball_nfl"


# Reuse the shared games-level markets as-is.
NFL_MARKETS: dict[str, FootballMarket] = dict(SHARED_FOOTBALL_MARKETS)


# Player-prop Odds API key mapping. Keys are the Odds API market
# strings; values are our canonical names from `PROP_MARKET_LABELS`.
NFL_PROP_ODDS_API_KEYS: dict[str, str] = {
    "player_pass_yds":       "Pass_Yds",
    "player_pass_tds":       "Pass_TDs",
    "player_pass_attempts":  "Pass_Att",
    "player_pass_completions": "Pass_Comp",
    "player_pass_interceptions": "Pass_Ints",
    "player_rush_yds":       "Rush_Yds",
    "player_rush_attempts":  "Rush_Att",
    "player_rush_tds":       "Rush_TDs",
    "player_reception_yds":  "Rec_Yds",
    "player_receptions":     "Rec_Recs",
    "player_reception_tds":  "Rec_TDs",
    "player_anytime_td":     "Anytime_TD",
    "player_longest_reception": "Longest_Rec",
}


# Mapping from MLB's market vocabulary to NFL's, for cross-engine
# parlay-building scaffolding once the parlay engine accepts
# multi-sport legs (future PR). Mostly empty — there's no clean
# semantic match between an NFL Spread and a NRFI total.
MLB_FOOTBALL_TO_NFL: dict[str, str] = {
    # Intentionally empty in F-1; cross-sport parlays come later.
}


__all__ = [
    "SPORT_KEY",
    "NFL_MARKETS",
    "NFL_PROP_ODDS_API_KEYS",
    "MLB_FOOTBALL_TO_NFL",
    "FootballMarket",
    "PROP_MARKET_LABELS",
]
