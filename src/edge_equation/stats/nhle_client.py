"""
NHL API client (api-web.nhle.com).

Parallel to MlbStatsClient. NHL's public API is free, comprehensive,
well-structured, and the canonical source for game results -- so we
use it directly for NHL instead of going through TheSportsDB, which
returns ~4% of NHL games even on the paid Patreon tier.

Endpoint shape mirrored from MlbStatsClient so the ingestor pattern
stays identical (fetch -> list of game dicts, cached via OddsCache,
throttled, retried on transient HTTP errors).

References:
  https://api-web.nhle.com/v1/score/YYYY-MM-DD
  -> {"games": [...], ...}
  Each game has id, startTimeUTC, gameState ("OFF" = officially over,
  "FINAL" = final), awayTeam / homeTeam with nested name.default and
  score.

Rate limiting: NHL API has no documented hard limit. We throttle
politely at the shared EE_MIN_REQUEST_INTERVAL_SEC value.
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date, datetime
from typing import Any, Dict, List, Optional

import httpx

from edge_equation.data_fetcher import (
    CACHE_TTL_SCHEDULE,
    _Throttle,
    _min_request_interval,
    _with_retries,
)
from edge_equation.persistence.odds_cache import OddsCache


NHLE_BASE = "https://api-web.nhle.com/v1"


class NhleClient:
    """Minimal NHL API wrapper. No auth required (open API)."""

    def __init__(
        self,
        http_client: Optional[httpx.Client] = None,
        base_url: str = NHLE_BASE,
        throttle: Optional[_Throttle] = None,
    ):
        self._base = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=15.0)
        self._throttle = throttle or _Throttle(_min_request_interval())

    def close(self) -> None:
        if self._owns_client:
            try:
                self._http.close()
            except Exception:
                pass

    def _cache_key(self, path: str) -> str:
        # Distinct prefix keeps NHL entries from colliding with MLB
        # Stats API or TheSportsDB entries in the OddsCache.
        return f"nhle:{path}"

    def _get(
        self,
        conn: sqlite3.Connection,
        path: str,
        ttl_seconds: int,
        now: Optional[datetime] = None,
        cached_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        cache_key = self._cache_key(path)
        cached = OddsCache.get(conn, cache_key, now=now)
        if cached is not None:
            return cached
        if cached_only:
            return None

        def _call() -> Dict[str, Any]:
            self._throttle.wait()
            url = f"{self._base}{path}"
            resp = self._http.get(url)
            resp.raise_for_status()
            return resp.json()

        payload = _with_retries(_call)
        if payload is None:
            return None
        OddsCache.put(conn, cache_key, payload, ttl_seconds=ttl_seconds, now=now)
        return payload

    def score_for_date(
        self,
        conn: sqlite3.Connection,
        day: _date,
        now: Optional[datetime] = None,
        cached_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """All NHL games for `day` including scores. Returns a flat
        list of game dicts. Empty list on failure / no games.

        Uses /score/<YYYY-MM-DD> which returns finalized scores for
        that date (unlike /schedule which returns a weekly view).
        """
        path = f"/score/{day.isoformat()}"
        payload = self._get(
            conn, path, ttl_seconds=CACHE_TTL_SCHEDULE,
            now=now, cached_only=cached_only,
        )
        if not payload:
            return []
        return list(payload.get("games") or [])
