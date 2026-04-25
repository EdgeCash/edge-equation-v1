"""
MLB Stats API client (statsapi.mlb.com).

Drop-in alternative to TheSportsDBClient for MLB game results. The free
TheSportsDB tier returns ~10% of MLB games per day; the paid Patreon
tier returns more but still incomplete coverage. MLB's own Stats API is
free, comprehensive, well-documented, and the canonical source -- so we
use it directly for MLB instead of going through a third-party
aggregator.

Endpoint shape mirrored from TheSportsDBClient so the ingestor pattern
stays identical (events_by_date -> list of game dicts, cached via
OddsCache, throttled, retried on transient HTTP errors).

References:
  https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD
  -> {"dates": [{"date": "...", "games": [...]}], ...}
  Each game has gamePk, gameDate, status.codedGameState ("F" = Final),
  teams.home/away.team.name, teams.home/away.score.

Rate limiting: MLB Stats API has no documented hard limit but the
operator credentials don't exist (it's an open API), so we throttle
politely at 0.2s between requests by default. The same EE_MIN_REQUEST_
INTERVAL_SEC env var that controls TheSportsDB throttling overrides
this when set.
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


MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
# MLB Stats API uses sport IDs to disambiguate league level. 1 = MLB.
MLB_SPORT_ID = 1


class MlbStatsClient:
    """Minimal MLB Stats API wrapper. No auth required (open API)."""

    def __init__(
        self,
        http_client: Optional[httpx.Client] = None,
        base_url: str = MLB_STATS_BASE,
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
        # Distinct prefix keeps MLB Stats entries from colliding with
        # TheSportsDB entries in the OddsCache.
        return f"mlb_stats:{path}"

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

    def schedule_for_date(
        self,
        conn: sqlite3.Connection,
        day: _date,
        now: Optional[datetime] = None,
        cached_only: bool = False,
        sport_id: int = MLB_SPORT_ID,
    ) -> List[Dict[str, Any]]:
        """All MLB games scheduled for `day`. Returns a flat list of
        game dicts (the API nests them under dates[].games[]; we
        flatten for the ingestor). Empty list on failure / no games.
        """
        path = f"/schedule?sportId={sport_id}&date={day.isoformat()}"
        payload = self._get(
            conn, path, ttl_seconds=CACHE_TTL_SCHEDULE,
            now=now, cached_only=cached_only,
        )
        if not payload:
            return []
        out: List[Dict[str, Any]] = []
        for date_block in payload.get("dates") or []:
            out.extend(date_block.get("games") or [])
        return out
