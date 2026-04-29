"""The Odds API source adapter for MLB player props.

This replaces the operational dependency on the legacy PrizePicks scraper.  It
keeps the adapter intentionally thin: fetching, caching, and auth live in the
shared core client, while this module declares the prop-market surface the
engine owns.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from edge_equation.engines.core.data.odds_api_client import TheOddsApiClient


SPORT_KEY_MLB = "baseball_mlb"

# Initial MLB batter/pitcher market set supported by The Odds API.  Additional
# markets can be added here without touching the engine runner.
MLB_PROPS_MARKETS: tuple[str, ...] = (
    "batter_home_runs",
    "batter_hits",
    "batter_total_bases",
    "batter_rbis",
    "batter_runs_scored",
    "batter_hits_runs_rbis",
    "pitcher_strikeouts",
    "pitcher_record_a_win",
)


@dataclass(frozen=True)
class PropsOddsApiSource:
    """Cache-first MLB props source backed by The Odds API."""

    conn: object
    sport_key: str = SPORT_KEY_MLB
    markets: Sequence[str] = MLB_PROPS_MARKETS
    regions: str = "us"
    ttl_seconds: int = 15 * 60
    api_key: Optional[str] = None
    cached_only: bool = False

    def fetch(self, *, http_client=None, now=None) -> dict:
        """Return raw Odds API payload for configured player-prop markets."""
        return TheOddsApiClient.fetch_odds(
            self.conn,
            sport_key=self.sport_key,
            markets=list(self.markets),
            regions=self.regions,
            ttl_seconds=self.ttl_seconds,
            api_key=self.api_key,
            http_client=http_client,
            now=now,
            cached_only=self.cached_only,
        )


@dataclass(frozen=True)
class PropMarketQuote:
    """One player-prop outcome quote normalized from The Odds API."""

    event_id: str
    commence_time: str
    home_team: str
    away_team: str
    bookmaker: str
    market_key: str
    player_name: str
    side: str
    line: Optional[Decimal]
    american_odds: float

    @property
    def game_label(self) -> str:
        return f"{self.away_team} @ {self.home_team}"


def normalize_prop_quotes(
    payload: dict,
    *,
    preferred_bookmaker: Optional[str] = None,
) -> list[PropMarketQuote]:
    """Flatten The Odds API prop payload into engine-owned quote objects."""

    quotes: list[PropMarketQuote] = []
    for game in payload.get("games", []):
        bookmaker = _select_bookmaker(
            game.get("bookmakers", []),
            preferred_bookmaker,
        )
        if bookmaker is None:
            continue
        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key", ""))
            if market_key not in MLB_PROPS_MARKETS:
                continue
            for outcome in market.get("outcomes", []):
                price = outcome.get("price")
                if price is None:
                    continue
                point = outcome.get("point")
                quotes.append(PropMarketQuote(
                    event_id=str(game.get("id", "")),
                    commence_time=str(game.get("commence_time", "")),
                    home_team=str(game.get("home_team", "")),
                    away_team=str(game.get("away_team", "")),
                    bookmaker=str(bookmaker.get("key", "")),
                    market_key=market_key,
                    player_name=str(
                        outcome.get("description")
                        or outcome.get("name")
                        or ""
                    ),
                    side=str(outcome.get("name", "")),
                    line=Decimal(str(point)) if point is not None else None,
                    american_odds=float(price),
                ))
    return quotes


def _select_bookmaker(
    bookmakers: Sequence[dict],
    preferred_bookmaker: Optional[str],
) -> Optional[dict]:
    if not bookmakers:
        return None
    if preferred_bookmaker:
        for book in bookmakers:
            if book.get("key") == preferred_bookmaker:
                return book
    return bookmakers[0]


__all__ = [
    "MLB_PROPS_MARKETS",
    "PropMarketQuote",
    "PropsOddsApiSource",
    "SPORT_KEY_MLB",
    "normalize_prop_quotes",
]
