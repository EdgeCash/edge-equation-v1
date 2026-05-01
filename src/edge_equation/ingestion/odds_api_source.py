"""
TheOddsApiSource: normalize The Odds API payloads into the raw-dict format
consumed by ingestion.normalizer.normalize_slate.

Sport-key -> internal league mapping:
    baseball_mlb          -> MLB
    basketball_nba        -> NBA
    basketball_ncaab      -> NCAAB
    americanfootball_nfl  -> NFL
    americanfootball_ncaaf-> NCAAF
    icehockey_nhl         -> NHL
    soccer_*              -> SOC  (prefix match covers EPL, UCL, etc.)

Market-key -> market_type mapping is sport-specific because Run_Line / Spread
/ Puck_Line are distinct internal market types for the same API "spreads"
concept.

Bookmaker selection: if preferred_bookmaker is set, use that book's quote;
else use the first bookmaker listed in the response. Median-across-books is
a future refinement; the current single-book approach is deterministic and
matches how most bettors consume a single sportsbook.
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from edge_equation.engines.core.data.odds_api_client import TheOddsApiClient


ODDS_API_SPORT_MAP = {
    "baseball_mlb": "MLB",
    "baseball_kbo": "KBO",
    "baseball_npb": "NPB",
    "basketball_nba": "NBA",
    "basketball_ncaab": "NCAAB",
    "americanfootball_nfl": "NFL",
    "americanfootball_ncaaf": "NCAAF",
    "icehockey_nhl": "NHL",
}

MARKET_KEY_MAP = {
    "MLB":   {"h2h": "ML", "spreads": "Run_Line", "totals": "Total"},
    "KBO":   {"h2h": "ML", "spreads": "Run_Line", "totals": "Total"},
    "NPB":   {"h2h": "ML", "spreads": "Run_Line", "totals": "Total"},
    "NBA":   {"h2h": "ML", "spreads": "Spread",   "totals": "Total"},
    "NCAAB": {"h2h": "ML", "spreads": "Spread",   "totals": "Total"},
    "NFL":   {"h2h": "ML", "spreads": "Spread",   "totals": "Total"},
    "NCAAF": {"h2h": "ML", "spreads": "Spread",   "totals": "Total"},
    "NHL":   {"h2h": "ML", "spreads": "Puck_Line", "totals": "Total"},
    "SOC":   {"h2h": "ML", "totals": "Total"},
}


class TheOddsApiSource:
    """
    Ingestion source backed by The Odds API (with cache-first client):
    - league_from_sport_key(sport_key)                  -> internal league code
    - get_raw_games(run_datetime=None)                  -> list of raw game dicts
    - get_raw_markets(run_datetime=None)                -> list of raw market dicts
    Constructor holds (conn, sport_key, markets, regions, preferred_bookmaker,
    ttl_seconds, api_key); every fetch rides the OddsCache.
    """

    def __init__(
        self,
        conn,
        sport_key: str,
        markets: Optional[List[str]] = None,
        regions: str = "us",
        preferred_bookmaker: Optional[str] = None,
        ttl_seconds: int = 6 * 60 * 60,   # 6h: matches DataFetcher.CACHE_TTL_ODDS
        api_key: Optional[str] = None,
        cached_only: bool = False,
    ):
        TheOddsApiSource.league_from_sport_key(sport_key)  # validates
        self.conn = conn
        self.sport_key = sport_key
        # Default markets MUST match the refresher's list
        # (data_fetcher._league_odds) so both sides compute the same
        # OddsCache key. A mismatch produces a silent cache miss: the
        # refresher writes fresh data under a key the cadence never
        # reads, and cadence's cached_only=True read returns empty ->
        # 0 picks in the email. Spreads covers Run_Line (MLB),
        # Puck_Line (NHL), and point Spread (NFL/NBA); soccer (SOC)
        # has no spreads mapping but the Odds API tolerates the extra
        # market param and the MARKET_KEY_MAP ignores unmapped keys.
        self.markets = (
            list(markets) if markets is not None else ["h2h", "totals", "spreads"]
        )
        self.regions = regions
        self.preferred_bookmaker = preferred_bookmaker
        self.ttl_seconds = ttl_seconds
        self.api_key = api_key
        # Credit guardrail: when True, a cache miss returns an empty
        # slate instead of hitting the live Odds API. Used by the five
        # cadence workflows so only the data-refresher ever burns a
        # credit.
        self.cached_only = cached_only

    @staticmethod
    def league_from_sport_key(sport_key: str) -> str:
        if sport_key in ODDS_API_SPORT_MAP:
            return ODDS_API_SPORT_MAP[sport_key]
        if sport_key.startswith("soccer_"):
            return "SOC"
        raise ValueError(
            f"Unsupported sport_key {sport_key!r}. "
            f"Known: {sorted(ODDS_API_SPORT_MAP.keys())} or any 'soccer_*'."
        )

    @staticmethod
    def _select_bookmaker(bookmakers: list, preferred: Optional[str]) -> Optional[dict]:
        if not bookmakers:
            return None
        if preferred is not None:
            for b in bookmakers:
                if b.get("key") == preferred:
                    return b
        return bookmakers[0]

    @staticmethod
    def _selection_name(api_market_key: str, outcome: dict) -> str:
        name = outcome.get("name", "")
        point = outcome.get("point")
        if api_market_key == "totals":
            # Totals selection carries the point inline ("Over 6.5")
            # because "Over" / "Under" alone isn't meaningful; the
            # posting formatter de-dupes if the line would append again.
            return f"{name} {point}" if point is not None else name
        # Spreads (Run_Line / Puck_Line / Spread) return just the team
        # name. The point lives on MarketInfo.line, and
        # BettingEngine._resolve_selection_side requires a strict team
        # match to decide whether to mirror the home-centric fair_prob.
        # Embedding the point here ("PIT -1.5") breaks that match and
        # silently drops the pick to fair_prob=None.
        return name

    @staticmethod
    def _normalize_game_markets(
        game: dict,
        league: str,
        preferred_bookmaker: Optional[str],
    ) -> list:
        bookmaker = TheOddsApiSource._select_bookmaker(
            game.get("bookmakers", []), preferred_bookmaker,
        )
        if bookmaker is None:
            return []
        market_map = MARKET_KEY_MAP.get(league, {})
        game_id = game["id"]
        result = []
        for market in bookmaker.get("markets", []):
            api_key = market.get("key")
            internal = market_map.get(api_key)
            if internal is None:
                continue
            for outcome in market.get("outcomes", []):
                price = outcome.get("price")
                if price is None:
                    continue
                point = outcome.get("point")
                line_value = Decimal(str(point)) if point is not None else None
                result.append({
                    "game_id": game_id,
                    "market_type": internal,
                    "selection": TheOddsApiSource._selection_name(api_key, outcome),
                    "line": line_value,
                    "odds": int(price),
                    "meta": {"bookmaker": bookmaker.get("key", "")},
                })
        return result

    def _payload(self, http_client=None, now=None) -> dict:
        return TheOddsApiClient.fetch_odds(
            self.conn,
            sport_key=self.sport_key,
            markets=self.markets,
            regions=self.regions,
            ttl_seconds=self.ttl_seconds,
            api_key=self.api_key,
            http_client=http_client,
            now=now,
            cached_only=self.cached_only,
        )

    def get_raw_games(self, run_datetime: Optional[datetime] = None, http_client=None, now=None) -> list:
        payload = self._payload(http_client=http_client, now=now)
        league = TheOddsApiSource.league_from_sport_key(self.sport_key)
        return [
            {
                "league": league,
                "game_id": g["id"],
                "start_time": g["commence_time"],
                "home_team": g["home_team"],
                "away_team": g["away_team"],
            }
            for g in payload.get("games", [])
        ]

    def get_raw_markets(self, run_datetime: Optional[datetime] = None, http_client=None, now=None) -> list:
        payload = self._payload(http_client=http_client, now=now)
        league = TheOddsApiSource.league_from_sport_key(self.sport_key)
        all_markets = []
        for g in payload.get("games", []):
            all_markets.extend(
                TheOddsApiSource._normalize_game_markets(g, league, self.preferred_bookmaker)
            )
        return all_markets
