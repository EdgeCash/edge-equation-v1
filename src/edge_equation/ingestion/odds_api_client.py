"""
The Odds API HTTP client with cache-first fetch.

Resolves odds payloads for a given (sport_key, markets, regions) combo from
the OddsCache when fresh; otherwise fetches from https://api.the-odds-api.com
and writes the result through to the cache.

Env vars:
- THE_ODDS_API_KEY  required unless api_key= is passed explicitly.

Cache keys are deterministic: "theoddsapi:{sport_key}:{m1,m2,...}:{regions}:{fmt}"
with markets sorted lexicographically for stable keying across argument order.

Design intent: keep this class stateless except for env-var plumbing so it
can be injected anywhere a connection is available. All network I/O goes
through one injectable httpx.Client -- tests pass a MockTransport-backed
client and never touch the real network.
"""
import os
from typing import Any, Dict, List, Optional

import httpx

from edge_equation.persistence.odds_cache import OddsCache


DEFAULT_ENDPOINT = "https://api.the-odds-api.com/v4/sports"
DEFAULT_TTL_SECONDS = 900  # 15 minutes on the free tier leaves plenty of headroom
DEFAULT_REGIONS = "us"
DEFAULT_ODDS_FORMAT = "american"
API_KEY_ENV_VAR = "THE_ODDS_API_KEY"


class TheOddsApiClient:
    """
    Thin HTTP wrapper with cache-first semantics:
    - cache_key(sport_key, markets, regions, odds_format) -> deterministic string
    - fetch_odds(conn, ...) -> payload dict (either from cache or fresh GET)
    - clear_cache(conn, sport_key=None) -> rows purged
    """

    @staticmethod
    def _resolve_api_key(override: Optional[str]) -> str:
        key = override if override is not None else os.environ.get(API_KEY_ENV_VAR)
        if not key:
            raise RuntimeError(
                f"Odds API key not set. Provide api_key= or export {API_KEY_ENV_VAR}."
            )
        return key

    @staticmethod
    def cache_key(
        sport_key: str,
        markets: List[str],
        regions: str = DEFAULT_REGIONS,
        odds_format: str = DEFAULT_ODDS_FORMAT,
    ) -> str:
        m = ",".join(sorted(markets))
        return f"theoddsapi:{sport_key}:{m}:{regions}:{odds_format}"

    @staticmethod
    def fetch_odds(
        conn,
        sport_key: str,
        markets: List[str],
        regions: str = DEFAULT_REGIONS,
        odds_format: str = DEFAULT_ODDS_FORMAT,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now=None,
        api_key: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        http_client: Optional[httpx.Client] = None,
    ) -> Dict[str, Any]:
        """
        Cache-first fetch. Returns {"games": [...]} where games mirrors the
        raw JSON array returned by The Odds API.

        If http_client is None, a short-lived httpx.Client is constructed and
        closed internally. Tests should pass an injected client (e.g. backed
        by httpx.MockTransport) so no network call is made.
        """
        key = TheOddsApiClient.cache_key(sport_key, markets, regions, odds_format)
        cached = OddsCache.get(conn, key, now=now)
        if cached is not None:
            return cached

        url = f"{endpoint}/{sport_key}/odds"
        params = {
            "apiKey": TheOddsApiClient._resolve_api_key(api_key),
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": odds_format,
            "dateFormat": "iso",
        }

        owns_client = http_client is None
        if owns_client:
            http_client = httpx.Client(timeout=30.0)
        try:
            resp = http_client.get(url, params=params)
            resp.raise_for_status()
            games = resp.json()
        finally:
            if owns_client:
                http_client.close()

        payload = {"games": games}
        OddsCache.put(conn, key, payload, ttl_seconds=ttl_seconds, now=now)
        return payload

    @staticmethod
    def clear_cache(conn, sport_key: Optional[str] = None) -> int:
        if sport_key is None:
            cur = conn.execute("DELETE FROM odds_cache WHERE cache_key LIKE 'theoddsapi:%'")
        else:
            like = f"theoddsapi:{sport_key}:%"
            cur = conn.execute("DELETE FROM odds_cache WHERE cache_key LIKE ?", (like,))
        conn.commit()
        return cur.rowcount
