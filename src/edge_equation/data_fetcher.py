"""
Safe, cached daily data fetcher.

One public entry point:

    fetch_daily_data(conn, date=None, slate="domestic", ...) -> DataBundle

Combines three sources under a single retry + rate-limit + cache umbrella:

  - The Odds API (existing TheOddsApiClient) -- odds / lines for every
    supported league including KBO and NPB via explicit sport_keys.
  - TheSportsDB (free tier, no auth required) -- schedules, team
    metadata, basic injury notes.
  - Lightweight cached scrapers for KBO (mykbostats.com) and NPB stats
    / starters. These are OPT-IN; the fetcher returns None for scraper
    keys when the caller sets scrape=False.

Caching: every remote call routes through the Phase 8 OddsCache (SQLite)
so repeat fetches within the TTL window never re-hit the network. This
mirrors the TheOddsApiClient Phase 9 pattern -- no new deps (no joblib),
single durable cache table, deterministic keying.

Rate limiting: a simple stdlib-sleep throttle between non-cached requests
(min 0.5s by default). For production deploys override via
MIN_REQUEST_INTERVAL_SEC env.

Retries: bounded exponential backoff (3 attempts, 1s / 2s / 4s). Any
remote failure that exceeds the retry budget degrades gracefully to
None in the DataBundle -- the engine must handle partial data.

public_mode: respected for the DataBundle.to_dict() serialization -- any
prop / line-detail fields a non-subscriber shouldn't see get stripped at
the bundle boundary.

No DataFrames here (keeps stdlib-only). The "dict of DataFrames" shape
the spec mentions can be materialized by the caller via
pandas.DataFrame(bundle.to_dict()[k]) if pandas is available; this module
returns plain list-of-dict payloads.

Phase 20 scope: scaffold + integration with OddsCache + TheSportsDB
happy path. KBO / NPB scrapers are interface-complete (abstract methods +
cache integration) but ship with "not implemented yet" fallbacks to keep
the build green. Wire real scraping endpoints in a follow-up.
"""
from dataclasses import dataclass, field
from datetime import date as _date, datetime
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

import httpx

from edge_equation.ingestion.odds_api_client import (
    DEFAULT_TTL_SECONDS,
    TheOddsApiClient,
)
from edge_equation.persistence.odds_cache import OddsCache


# ---------------------------------------------- constants & config

THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"
THESPORTSDB_FREE_KEY = "3"   # public free-tier key per their docs

# TheSportsDB league ids we care about.
THESPORTSDB_LEAGUE_IDS = {
    "MLB": 4424,
    "NBA": 4387,
    "NFL": 4391,
    "NHL": 4380,
    "KBO": 4830,
    "NPB": 4831,
    "EPL": 4328,
    "UCL": 4480,
}

# Odds-API sport_keys by slate. "domestic" covers the US majors that The
# Odds API free tier supports out-of-the-box. "overseas" is every league
# we actively publish on X but that may need a mix of Odds API +
# scrapers.
SLATE_SPORTS = {
    "domestic": ("MLB", "NFL", "NBA", "NHL"),
    "overseas": ("KBO", "NPB", "EPL", "UCL"),
}

ODDS_API_SPORT_KEY = {
    "MLB": "baseball_mlb",
    "NFL": "americanfootball_nfl",
    "NBA": "basketball_nba",
    "NHL": "icehockey_nhl",
    "KBO": "baseball_kbo",
    "NPB": "baseball_npb",
    "EPL": "soccer_epl",
    "UCL": "soccer_uefa_champs_league",
}

# Cache TTLs per data class -- longer for semi-static metadata, shorter
# for odds that move through the day.
CACHE_TTL_ODDS = DEFAULT_TTL_SECONDS          # 15 minutes
CACHE_TTL_SCHEDULE = 4 * 60 * 60              # 4 hours
CACHE_TTL_SCRAPER = 60 * 60                   # 1 hour

ENV_MIN_REQUEST_INTERVAL = "EE_MIN_REQUEST_INTERVAL_SEC"
DEFAULT_MIN_REQUEST_INTERVAL_SEC = 0.5

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_SEC = 1.0


# ---------------------------------------------- rate limiting + retries

class _Throttle:
    """Simple per-source throttle -- enforce min interval between calls."""

    def __init__(self, min_interval_sec: float):
        self._min = max(0.0, float(min_interval_sec))
        self._last_call_ts = 0.0
        self._sleep = time.sleep

    def wait(self) -> None:
        if self._min <= 0.0:
            return
        now = time.monotonic()
        delta = now - self._last_call_ts
        if delta < self._min:
            self._sleep(self._min - delta)
        self._last_call_ts = time.monotonic()


def _min_request_interval() -> float:
    raw = os.environ.get(ENV_MIN_REQUEST_INTERVAL)
    if raw is None:
        return DEFAULT_MIN_REQUEST_INTERVAL_SEC
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_MIN_REQUEST_INTERVAL_SEC


def _with_retries(
    fn,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE_SEC,
    sleep=time.sleep,
):
    """
    Run fn() with bounded exponential backoff. Returns fn()'s result on
    success or None if every attempt raised. Retries on any Exception
    subclass the fetcher considers transient (httpx transport + status
    errors); other exceptions propagate immediately.
    """
    transient = (
        httpx.TransportError,
        httpx.HTTPStatusError,
        httpx.TimeoutException,
    )
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return fn()
        except transient as e:
            last_err = e
            if attempt == max_retries - 1:
                break
            sleep(backoff_base * (2 ** attempt))
    return None


# ---------------------------------------------- TheSportsDB client

class TheSportsDBClient:
    """
    Minimal TheSportsDB wrapper. Free tier, no auth; key "3" is the
    documented public key. Calls are cached via OddsCache under distinct
    prefixes so they don't collide with The Odds API entries.
    """

    def __init__(
        self,
        http_client: Optional[httpx.Client] = None,
        api_key: str = THESPORTSDB_FREE_KEY,
        base_url: str = THESPORTSDB_BASE,
        throttle: Optional[_Throttle] = None,
    ):
        self._api_key = api_key
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
        return f"thesportsdb:{path}"

    def _get(
        self,
        conn: sqlite3.Connection,
        path: str,
        ttl_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        cache_key = self._cache_key(path)
        cached = OddsCache.get(conn, cache_key, now=now)
        if cached is not None:
            return cached

        def _call() -> Dict[str, Any]:
            self._throttle.wait()
            url = f"{self._base}/{self._api_key}{path}"
            resp = self._http.get(url)
            resp.raise_for_status()
            return resp.json()

        payload = _with_retries(_call)
        if payload is None:
            return None
        OddsCache.put(conn, cache_key, payload, ttl_seconds=ttl_seconds, now=now)
        return payload

    # -------------------------------------------- endpoints

    def events_by_date(
        self,
        conn: sqlite3.Connection,
        day: _date,
        league_id: int,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        All events for a league on a given day. Returns a list of raw
        event dicts (empty list on failure / no events).
        """
        path = f"/eventsday.php?d={day.isoformat()}&l={league_id}"
        payload = self._get(conn, path, ttl_seconds=CACHE_TTL_SCHEDULE, now=now)
        if not payload:
            return []
        return list(payload.get("events") or [])

    def team_by_id(
        self,
        conn: sqlite3.Connection,
        team_id: int,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        path = f"/lookupteam.php?id={team_id}"
        payload = self._get(conn, path, ttl_seconds=CACHE_TTL_SCHEDULE * 6, now=now)
        if not payload:
            return None
        teams = payload.get("teams") or []
        return teams[0] if teams else None


# ---------------------------------------------- scrapers (skeleton)


class KboStatsScraper:
    """
    Scraper wrapper for mykbostats.com (KBO). Phase 20 ships the cached
    interface; the HTML parsing itself is intentionally stubbed so the
    build stays green without a live target page. When you wire a real
    parser, do so inside _parse() and keep the cache/retry envelope.
    """

    BASE = "https://www.mykbostats.com"
    CACHE_PREFIX = "scraper:kbo"

    def __init__(
        self,
        http_client: Optional[httpx.Client] = None,
        throttle: Optional[_Throttle] = None,
    ):
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=15.0)
        self._throttle = throttle or _Throttle(_min_request_interval())

    def close(self) -> None:
        if self._owns_client:
            try:
                self._http.close()
            except Exception:
                pass

    def _fetch(
        self,
        conn: sqlite3.Connection,
        path: str,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        cache_key = f"{self.CACHE_PREFIX}:{path}"
        cached = OddsCache.get(conn, cache_key, now=now)
        if cached is not None:
            return cached.get("html")

        def _call() -> str:
            self._throttle.wait()
            resp = self._http.get(f"{self.BASE}{path}")
            resp.raise_for_status()
            return resp.text

        html = _with_retries(_call)
        if html is None:
            return None
        OddsCache.put(conn, cache_key, {"html": html}, ttl_seconds=CACHE_TTL_SCRAPER, now=now)
        return html

    @staticmethod
    def _parse(html: Optional[str]) -> List[Dict[str, Any]]:
        # Intentional stub: parsing logic lives outside this commit until
        # we have a real target page to validate against. Returns an
        # empty list so callers degrade gracefully.
        return []

    def starters_for_day(
        self,
        conn: sqlite3.Connection,
        day: _date,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Returns a list of starter records for the day (empty in the
        stub)."""
        path = f"/schedule/{day.isoformat()}"
        html = self._fetch(conn, path, now=now)
        return KboStatsScraper._parse(html)


class NpbStatsScraper:
    """
    NPB stats/starters scraper wrapper. Same shape as KboStatsScraper --
    cached + retried + rate-limited envelope; parsing is a stub until we
    point it at a specific source.
    """

    BASE = "https://npb.jp"
    CACHE_PREFIX = "scraper:npb"

    def __init__(
        self,
        http_client: Optional[httpx.Client] = None,
        throttle: Optional[_Throttle] = None,
    ):
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=15.0)
        self._throttle = throttle or _Throttle(_min_request_interval())

    def close(self) -> None:
        if self._owns_client:
            try:
                self._http.close()
            except Exception:
                pass

    def _fetch(
        self,
        conn: sqlite3.Connection,
        path: str,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        cache_key = f"{self.CACHE_PREFIX}:{path}"
        cached = OddsCache.get(conn, cache_key, now=now)
        if cached is not None:
            return cached.get("html")

        def _call() -> str:
            self._throttle.wait()
            resp = self._http.get(f"{self.BASE}{path}")
            resp.raise_for_status()
            return resp.text

        html = _with_retries(_call)
        if html is None:
            return None
        OddsCache.put(conn, cache_key, {"html": html}, ttl_seconds=CACHE_TTL_SCRAPER, now=now)
        return html

    @staticmethod
    def _parse(html: Optional[str]) -> List[Dict[str, Any]]:
        return []

    def starters_for_day(
        self,
        conn: sqlite3.Connection,
        day: _date,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        path = f"/bis/eng/{day.year}/games/s{day.strftime('%m%d')}.html"
        html = self._fetch(conn, path, now=now)
        return NpbStatsScraper._parse(html)


# ---------------------------------------------- DataBundle + fetch_daily_data

@dataclass(frozen=True)
class DataBundle:
    """
    Per-league bucketed raw payloads. Every key maps to a list-of-dicts
    (empty when the source failed gracefully). Phase 20 keeps it as plain
    stdlib types so pandas is optional -- the caller wraps each list in
    pd.DataFrame(...) only if they want DataFrame semantics.
    """
    date: str
    slate: str
    odds: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    schedules: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    scrapers: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    public_mode: bool = False

    def to_dict(self) -> dict:
        """JSON-friendly view. public_mode=True strips any per-book raw
        price data so free consumers only see market consensus summaries."""
        odds = self.odds
        if self.public_mode:
            # Shallow sanitization: remove raw bookmaker lists from each
            # game, keep everything else. Callers that render consensus
            # totals / moneylines work off higher-level derived fields.
            odds = {}
            for league, games in self.odds.items():
                sanitized = []
                for g in games:
                    g2 = {k: v for k, v in g.items() if k != "bookmakers"}
                    sanitized.append(g2)
                odds[league] = sanitized
        return {
            "date": self.date,
            "slate": self.slate,
            "odds": odds,
            "schedules": self.schedules,
            "scrapers": self.scrapers,
            "public_mode": self.public_mode,
        }


def _league_odds(
    conn: sqlite3.Connection,
    league: str,
    api_client: Optional[TheOddsApiClient],
    api_key: Optional[str],
    http_client: Optional[httpx.Client],
    now: Optional[datetime],
) -> List[Dict[str, Any]]:
    sport_key = ODDS_API_SPORT_KEY.get(league)
    if sport_key is None:
        return []
    markets = ["h2h", "totals", "spreads"] if league != "Soccer" else ["h2h", "totals"]

    def _call() -> Dict[str, Any]:
        payload = TheOddsApiClient.fetch_odds(
            conn,
            sport_key=sport_key,
            markets=markets,
            api_key=api_key,
            ttl_seconds=CACHE_TTL_ODDS,
            now=now,
            http_client=http_client,
        )
        return payload

    result = _with_retries(_call)
    if result is None:
        return []
    return list(result.get("games") or [])


def fetch_daily_data(
    conn: sqlite3.Connection,
    date: Optional[_date] = None,
    slate: str = "domestic",
    public_mode: bool = True,
    api_key: Optional[str] = None,
    http_client: Optional[httpx.Client] = None,
    sportsdb_client: Optional[TheSportsDBClient] = None,
    kbo_scraper: Optional[KboStatsScraper] = None,
    npb_scraper: Optional[NpbStatsScraper] = None,
    now: Optional[datetime] = None,
    scrape: bool = True,
) -> DataBundle:
    """
    One-stop safe-and-cached daily pull. Returns a DataBundle whose
    fields are lists-of-dicts (never None -- the fetcher degrades to []
    on any transient failure).

    Args:
      conn         Persistence connection (SQLite or Turso adapter).
                   OddsCache + migrations must already be applied.
      date         Target slate date. Defaults to UTC today.
      slate        "domestic" or "overseas".
      public_mode  Whether DataBundle.to_dict() sanitizes output.
      api_key      Explicit Odds API key; env var THE_ODDS_API_KEY
                   is the fallback.
      http_client  Injectable httpx.Client for deterministic tests.
      scrape       When False, skip KBO/NPB scrapers entirely.
    """
    if slate not in SLATE_SPORTS:
        raise ValueError(
            f"slate must be one of {list(SLATE_SPORTS.keys())}, got {slate!r}"
        )
    target_date = date or datetime.utcnow().date()

    leagues = SLATE_SPORTS[slate]
    odds: Dict[str, List[Dict[str, Any]]] = {}
    for league in leagues:
        odds[league] = _league_odds(
            conn=conn,
            league=league,
            api_client=None,
            api_key=api_key,
            http_client=http_client,
            now=now,
        )

    sdb = sportsdb_client or TheSportsDBClient(http_client=http_client)
    schedules: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for league in leagues:
            league_id = THESPORTSDB_LEAGUE_IDS.get(league)
            if league_id is None:
                schedules[league] = []
                continue
            schedules[league] = sdb.events_by_date(
                conn, day=target_date, league_id=league_id, now=now,
            )
    finally:
        if sportsdb_client is None:
            sdb.close()

    scrapers: Dict[str, List[Dict[str, Any]]] = {}
    if scrape:
        if slate == "overseas" or "KBO" in leagues:
            scr = kbo_scraper or KboStatsScraper(http_client=http_client)
            try:
                scrapers["KBO"] = scr.starters_for_day(conn, target_date, now=now)
            finally:
                if kbo_scraper is None:
                    scr.close()
        if slate == "overseas" or "NPB" in leagues:
            scr = npb_scraper or NpbStatsScraper(http_client=http_client)
            try:
                scrapers["NPB"] = scr.starters_for_day(conn, target_date, now=now)
            finally:
                if npb_scraper is None:
                    scr.close()

    return DataBundle(
        date=target_date.isoformat(),
        slate=slate,
        odds=odds,
        schedules=schedules,
        scrapers=scrapers,
        public_mode=public_mode,
    )
