"""
The Odds API HTTP client with cache-first fetch.

This shared-core module is the canonical client for all engines.  The legacy
``edge_equation.ingestion.odds_api_client`` module re-exports this class so
existing ingestion paths and tests keep working while engine-owned source
packages move to ``edge_equation.engines.core``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from edge_equation.engines.core.caching.odds_cache import OddsCache


DEFAULT_ENDPOINT = "https://api.the-odds-api.com/v4/sports"
DEFAULT_TTL_SECONDS = 900
DEFAULT_REGIONS = "us"
DEFAULT_ODDS_FORMAT = "american"
API_KEY_ENV_VAR = "THE_ODDS_API_KEY"


class TheOddsApiClient:
    """Thin HTTP wrapper with cache-first semantics."""

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
        market_key = ",".join(sorted(markets))
        return f"theoddsapi:{sport_key}:{market_key}:{regions}:{odds_format}"

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
        cached_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Return ``{"games": [...]}`` from cache when fresh, otherwise fetch live.

        ``cached_only=True`` is a credit guardrail for scheduled consumers that
        should read only what a data-refresh job has already stored.
        """
        key = TheOddsApiClient.cache_key(sport_key, markets, regions, odds_format)
        cached = OddsCache.get(conn, key, now=now)
        if cached is not None:
            return cached
        if cached_only:
            return {"games": []}

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
            cur = conn.execute(
                "DELETE FROM odds_cache WHERE cache_key LIKE 'theoddsapi:%'"
            )
        else:
            like = f"theoddsapi:{sport_key}:%"
            cur = conn.execute("DELETE FROM odds_cache WHERE cache_key LIKE ?", (like,))
        conn.commit()
        return cur.rowcount


__all__ = [
    "API_KEY_ENV_VAR",
    "DEFAULT_ENDPOINT",
    "DEFAULT_ODDS_FORMAT",
    "DEFAULT_REGIONS",
    "DEFAULT_TTL_SECONDS",
    "TheOddsApiClient",
]
