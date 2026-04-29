"""The Odds API source adapter for MLB player props.

This replaces the operational dependency on the legacy PrizePicks scraper.  It
keeps the adapter intentionally thin: fetching, caching, and auth live in the
shared core client, while this module declares the prop-market surface the
engine owns.
"""

from __future__ import annotations

from dataclasses import dataclass
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


__all__ = ["MLB_PROPS_MARKETS", "PropsOddsApiSource", "SPORT_KEY_MLB"]
