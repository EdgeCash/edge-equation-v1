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
from html.parser import HTMLParser
import os
import re
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

# Cache TTLs per data class. Odds TTL is 6h so the five cadence slots
# across a typical posting day (9a / 11a / 4p / 6p / 11p CT) all read
# from a single data-refresher pull rather than each one burning a
# fresh Odds API call. The refresher job is responsible for writing
# fresh payloads at least twice daily.
CACHE_TTL_ODDS = 6 * 60 * 60                  # 6 hours
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
    Minimal TheSportsDB wrapper. The constructor resolves the API key
    in this precedence order:
        1. Explicit `api_key` argument (used by tests for dependency
           injection).
        2. THESPORTSDB_API_KEY environment variable -- production path
           where the operator's paid Patreon key is provisioned via the
           workflow secret of the same name.
        3. THESPORTSDB_FREE_KEY ("3") -- the documented public key, used
           as a last-resort fallback so a missing env var never crashes
           the engine, just thins the data.
    Calls are cached via OddsCache under distinct prefixes so they
    don't collide with The Odds API entries.
    """

    def __init__(
        self,
        http_client: Optional[httpx.Client] = None,
        api_key: Optional[str] = None,
        base_url: str = THESPORTSDB_BASE,
        throttle: Optional[_Throttle] = None,
    ):
        if api_key is None:
            api_key = os.getenv("THESPORTSDB_API_KEY") or THESPORTSDB_FREE_KEY
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
        cached_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        cache_key = self._cache_key(path)
        cached = OddsCache.get(conn, cache_key, now=now)
        if cached is not None:
            return cached
        if cached_only:
            # Cadence-read path: never hit the network. Return None so
            # the caller degrades to an empty event list.
            return None

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
        cached_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        All events for a league on a given day. Returns a list of raw
        event dicts (empty list on failure / no events).
        """
        path = f"/eventsday.php?d={day.isoformat()}&l={league_id}"
        payload = self._get(
            conn, path, ttl_seconds=CACHE_TTL_SCHEDULE,
            now=now, cached_only=cached_only,
        )
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


# ---------------------------------------------- scrapers


class _TableRowParser(HTMLParser):
    """
    Lightweight stdlib HTML scraper: collect one row of text cells per
    `<tr>` inside EVERY matching `<table>` in the document. Tolerates
    malformed markup -- anything that doesn't fit the table shape is
    silently skipped, and we never raise out of the parser. Callers are
    expected to map the resulting rows to domain dicts.

    Table matching: if `table_class` is provided, only tables whose class
    list contains that token participate. If None, every `<table>` does.
    Row matching: if `row_class` is set, only <tr> tags carrying that
    class are collected. If None, every <tr> inside a matching table is
    collected. Nested tables are flattened -- we count table depth so
    rows inside an inner table still resolve against the outer scope.
    Rows with zero cells (header-only rows that render no <td>) are
    dropped so malformed decorative rows don't pollute the output.
    """

    def __init__(
        self,
        table_class: Optional[str] = None,
        row_class: Optional[str] = None,
    ):
        super().__init__(convert_charrefs=True)
        self._want_table_cls = table_class
        self._want_row_cls = row_class
        self._table_depth = 0
        self._in_row = False
        self._in_cell = False
        self._current_row: List[str] = []
        self._cell_buf: List[str] = []
        self.rows: List[List[str]] = []

    @staticmethod
    def _has_class(attrs: List, name: Optional[str]) -> bool:
        if name is None:
            return True
        for k, v in attrs:
            if k == "class" and v and name in v.split():
                return True
        return False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._has_class(attrs, self._want_table_cls):
                self._table_depth += 1
            return
        if self._table_depth <= 0:
            return
        if tag == "tr" and self._has_class(attrs, self._want_row_cls):
            self._in_row = True
            self._current_row = []
            return
        if self._in_row and tag in ("td", "th"):
            self._in_cell = True
            self._cell_buf = []

    def handle_endtag(self, tag):
        if tag == "table" and self._table_depth > 0:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_row = False
            return
        if tag == "tr" and self._in_row:
            if self._current_row:
                self.rows.append(self._current_row)
            self._in_row = False
            return
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            cell_text = " ".join("".join(self._cell_buf).split())
            self._current_row.append(cell_text)

    def handle_data(self, data):
        if self._in_cell:
            self._cell_buf.append(data)


def _parse_table_rows(
    html: str,
    table_class: Optional[str] = None,
    row_class: Optional[str] = None,
) -> List[List[str]]:
    """Parse rows from the first matching table; returns [] on any error."""
    try:
        p = _TableRowParser(table_class=table_class, row_class=row_class)
        p.feed(html)
        p.close()
        return p.rows
    except Exception:
        return []


# mykbostats.com daily schedule renders two common shapes:
#   (A) <tr class="game"><td>18:30</td><td>LG @ Doosan</td><td>Kelly / Raley</td></tr>
#   (B) <tr><td>18:30</td><td>LG</td><td>@</td><td>Doosan</td><td>Kelly</td><td>/</td><td>Raley</td></tr>
# Shape (A) puts the matchup in a single cell. Shape (B) spreads the
# matchup across team/separator/team cells and the starters across
# name/separator/name cells. Both shapes resolve to the same normalized
# output dict below.
_KBO_MATCHUP_SPLIT_RE = re.compile(r"\s+(?:@|vs\.?)\s+", re.IGNORECASE)
_KBO_STARTERS_SPLIT_RE = re.compile(r"\s*(?:/|vs\.?)\s*", re.IGNORECASE)
_TIME_CELL_RE = re.compile(r"^\d{1,2}:\d{2}(?:\s*(?:AM|PM))?$", re.IGNORECASE)


def _kbo_row_to_game(row: List[str], day_iso: str) -> Optional[Dict[str, Any]]:
    """Map one row of text cells to a game dict; return None if the row
    isn't shaped like a game (headers, spacers, cancelled games)."""
    if len(row) < 2:
        return None
    # Drop empty cells and surrounding whitespace; real HTML often has
    # blank <td></td> separators we don't care about.
    cells = [c.strip() for c in row if c and c.strip()]
    if len(cells) < 2:
        return None
    start_time = cells[0]
    if not _TIME_CELL_RE.match(start_time):
        # Row doesn't start with a HH:MM time -> likely a header or label row.
        return None

    away_team = home_team = ""
    away_starter = home_starter = ""

    # Shape A: one matchup cell with embedded separator.
    matchup_cell = cells[1] if len(cells) > 1 else ""
    teams = _KBO_MATCHUP_SPLIT_RE.split(matchup_cell, maxsplit=1)
    if len(teams) == 2:
        away_team, home_team = teams[0].strip(), teams[1].strip()
        starters_raw = cells[2] if len(cells) >= 3 else ""
        if starters_raw:
            parts = _KBO_STARTERS_SPLIT_RE.split(starters_raw, maxsplit=1)
            if len(parts) == 2:
                away_starter, home_starter = parts[0].strip(), parts[1].strip()
    elif len(cells) >= 4 and cells[2] in ("@", "vs", "vs."):
        # Shape B: team / separator / team across three cells.
        away_team, home_team = cells[1], cells[3]
        # Starters may span cells[4..] using the same pattern (name, sep, name)
        if len(cells) >= 7 and cells[5] in ("/", "vs", "vs."):
            away_starter, home_starter = cells[4], cells[6]
        elif len(cells) >= 5:
            starters_raw = " ".join(cells[4:])
            parts = _KBO_STARTERS_SPLIT_RE.split(starters_raw, maxsplit=1)
            if len(parts) == 2:
                away_starter, home_starter = parts[0].strip(), parts[1].strip()
    else:
        return None

    if not (away_team and home_team):
        return None
    return {
        "league": "KBO",
        "game_id": f"KBO-{day_iso}-{away_team}-{home_team}",
        "start_time_local": start_time,
        "home_team": home_team,
        "away_team": away_team,
        "home_starter": home_starter,
        "away_starter": away_starter,
    }


def _parse_kbo_rows(rows: List[List[str]], day_iso: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for row in rows:
        game = _kbo_row_to_game(row, day_iso)
        if game is None:
            continue
        gid = game["game_id"]
        if gid in seen_ids:
            continue
        seen_ids.add(gid)
        out.append(game)
    return out


# npb.jp BIS English schedule: table order is
# [time, away team, (score?), home team, (stadium?), starters]. Starters
# may be blank for future games, or carry a single name with no separator
# for completed games. Alternate pages render a stacked layout we don't
# try to interpret -- those rows return None.
_NPB_STARTERS_SPLIT_RE = re.compile(r"\s*(?:vs\.?|/|-)\s*", re.IGNORECASE)


def _npb_row_to_game(row: List[str], day_iso: str) -> Optional[Dict[str, Any]]:
    """Map one NPB BIS row to a game dict; drop headers/decorative rows."""
    cells = [c.strip() for c in row if c and c.strip()]
    if len(cells) < 3:
        return None
    start_time = cells[0]
    if not _TIME_CELL_RE.match(start_time):
        return None
    # Try the full six-cell layout first (time, away, score, home, stadium?, starters).
    away_team = cells[1] if len(cells) > 1 else ""
    home_team = ""
    starters_raw = ""
    if len(cells) >= 4:
        # Cell 2 is often the score for completed games ("5-3") or "-" for
        # scheduled games. Cell 3 is the home team.
        home_team = cells[3]
        starters_raw = cells[-1] if len(cells) >= 5 and cells[-1] != home_team else ""
    elif len(cells) == 3:
        # Compact layout: time / away / home.
        home_team = cells[2]
    if not (away_team and home_team):
        return None
    away_starter, home_starter = "", ""
    if starters_raw and not _TIME_CELL_RE.match(starters_raw):
        parts = _NPB_STARTERS_SPLIT_RE.split(starters_raw, maxsplit=1)
        if len(parts) == 2:
            away_starter, home_starter = parts[0].strip(), parts[1].strip()
    return {
        "league": "NPB",
        "game_id": f"NPB-{day_iso}-{away_team}-{home_team}",
        "start_time_local": start_time,
        "home_team": home_team,
        "away_team": away_team,
        "home_starter": home_starter,
        "away_starter": away_starter,
    }


def _parse_npb_rows(rows: List[List[str]], day_iso: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for row in rows:
        game = _npb_row_to_game(row, day_iso)
        if game is None:
            continue
        gid = game["game_id"]
        if gid in seen_ids:
            continue
        seen_ids.add(gid)
        out.append(game)
    return out


class KboStatsScraper:
    """
    Scraper wrapper for mykbostats.com. Targets the daily schedule page
    (.../schedule/YYYY-MM-DD) and returns a list of per-game dicts with
    teams, start time, and probable starters. Lightweight stdlib HTML
    parsing only -- no bs4, no external deps.

    On any parse failure the scraper returns an empty list so the rest
    of the fetcher degrades gracefully. See _parse_kbo_rows for the
    output shape.
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
        cached_only: bool = False,
    ) -> Optional[str]:
        cache_key = f"{self.CACHE_PREFIX}:{path}"
        cached = OddsCache.get(conn, cache_key, now=now)
        if cached is not None:
            return cached.get("html")
        if cached_only:
            return None

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

    # Known real-world table class variants in priority order. The
    # scraper walks them top-to-bottom and uses the first that yields at
    # least one valid game row. Keep the list tight so the first call
    # doesn't waste work on layouts that never ship.
    _TABLE_CLASS_CANDIDATES = (
        "schedule",
        "games-schedule",
        "daily-schedule",
        "game-list",
        "table",   # generic; hit via fallback only when nothing else matched
    )

    @staticmethod
    def _parse(html: Optional[str], day_iso: str = "") -> List[Dict[str, Any]]:
        """Parse mykbostats daily schedule HTML -> list of game dicts.

        The scraper tries multiple table-class candidates because
        mykbostats has shipped at least two distinct layouts. If none
        match, it falls back to a blanket "every <table>" pass so a
        simple unstyled table still parses. Row filtering by game/row
        shape happens in _kbo_row_to_game -- the parser here is purely
        a structural dragnet.
        """
        if not html:
            return []
        for cls in KboStatsScraper._TABLE_CLASS_CANDIDATES:
            rows = _parse_table_rows(html, table_class=cls)
            games = _parse_kbo_rows(rows, day_iso=day_iso)
            if games:
                return games
        # Final fallback: scan every <table> in the document.
        rows = _parse_table_rows(html)
        return _parse_kbo_rows(rows, day_iso=day_iso)

    def starters_for_day(
        self,
        conn: sqlite3.Connection,
        day: _date,
        now: Optional[datetime] = None,
        cached_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return a list of starter records for the day (empty on any
        network or parse failure)."""
        path = f"/schedule/{day.isoformat()}"
        html = self._fetch(conn, path, now=now, cached_only=cached_only)
        return KboStatsScraper._parse(html, day_iso=day.isoformat())


class NpbStatsScraper:
    """
    NPB stats/starters scraper wrapper targeting npb.jp's English BIS
    pages (.../bis/eng/YYYY/games/sMMDD.html). Returns a list of per-game
    dicts identical in shape to KboStatsScraper output (game_id, teams,
    starters, start_time). Lightweight stdlib HTML parsing only.

    On any parse failure the scraper returns an empty list so the rest
    of the fetcher degrades gracefully.
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
        cached_only: bool = False,
    ) -> Optional[str]:
        cache_key = f"{self.CACHE_PREFIX}:{path}"
        cached = OddsCache.get(conn, cache_key, now=now)
        if cached is not None:
            return cached.get("html")
        if cached_only:
            return None

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

    _TABLE_CLASS_CANDIDATES = (
        "schedule_table",
        "teble",         # alt misspelled-class layout seen in the wild
        "schedule",
        "game_list",
        "games",
    )

    @staticmethod
    def _parse(html: Optional[str], day_iso: str = "") -> List[Dict[str, Any]]:
        """Parse the NPB English BIS daily schedule -> list of game dicts.

        NPB's BIS page has shipped multiple table shapes across seasons
        (schedule_table, schedule, a misspelled "teble" class). We walk
        the known candidates, then fall back to every <table>, and let
        _npb_row_to_game filter header/decorative rows.
        """
        if not html:
            return []
        for cls in NpbStatsScraper._TABLE_CLASS_CANDIDATES:
            rows = _parse_table_rows(html, table_class=cls)
            games = _parse_npb_rows(rows, day_iso=day_iso)
            if games:
                return games
        rows = _parse_table_rows(html)
        return _parse_npb_rows(rows, day_iso=day_iso)

    def starters_for_day(
        self,
        conn: sqlite3.Connection,
        day: _date,
        now: Optional[datetime] = None,
        cached_only: bool = False,
    ) -> List[Dict[str, Any]]:
        path = f"/bis/eng/{day.year}/games/s{day.strftime('%m%d')}.html"
        html = self._fetch(conn, path, now=now, cached_only=cached_only)
        return NpbStatsScraper._parse(html, day_iso=day.isoformat())


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
    cached_only: bool = False,
) -> List[Dict[str, Any]]:
    sport_key = ODDS_API_SPORT_KEY.get(league)
    if sport_key is None:
        return []
    markets = ["h2h", "totals", "spreads"] if league != "Soccer" else ["h2h", "totals"]

    def _call() -> Dict[str, Any]:
        # cached_only=True flows straight through to the cache-first
        # Odds API client so the cadence workflows never hit the live
        # API on a cold cache -- they degrade to empty and wait for
        # the next data-refresher run.
        payload = TheOddsApiClient.fetch_odds(
            conn,
            sport_key=sport_key,
            markets=markets,
            api_key=api_key,
            ttl_seconds=CACHE_TTL_ODDS,
            now=now,
            http_client=http_client,
            cached_only=cached_only,
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
    cached_only: bool = False,
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
            cached_only=cached_only,
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
                cached_only=cached_only,
            )
    finally:
        if sportsdb_client is None:
            sdb.close()

    scrapers: Dict[str, List[Dict[str, Any]]] = {}
    if scrape:
        if slate == "overseas" or "KBO" in leagues:
            scr = kbo_scraper or KboStatsScraper(http_client=http_client)
            try:
                scrapers["KBO"] = scr.starters_for_day(
                    conn, target_date, now=now, cached_only=cached_only,
                )
            finally:
                if kbo_scraper is None:
                    scr.close()
        if slate == "overseas" or "NPB" in leagues:
            scr = npb_scraper or NpbStatsScraper(http_client=http_client)
            try:
                scrapers["NPB"] = scr.starters_for_day(
                    conn, target_date, now=now, cached_only=cached_only,
                )
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
