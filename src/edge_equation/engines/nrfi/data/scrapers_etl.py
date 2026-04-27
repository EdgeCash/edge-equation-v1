"""Free / public data ingestion for the NRFI engine.

Sources
-------
* MLB Stats API (statsapi.mlb.com) — schedule, probable pitchers,
  boxscores (lineups + umpires), linescore (first-inning runs), people.
  No API key required; just a polite UA + rate limit.
* pybaseball — pitcher first-inning Statcast splits, leaderboards.
* Baseball Savant ABS dashboard — 2026 challenge / overturn rates per
  umpire and team. HTML scrape (the public dashboard exposes a JSON
  endpoint behind it; we hit that directly when the schema is stable
  and fall back to HTML parsing otherwise).

All network IO routes through `nrfi.utils.rate_limit.global_limiter`.
Heavyweight pulls (Statcast pitch-level frames) are cached as parquet
under `cache_dir/parquet/<namespace>/`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Optional

import httpx

from ..config import APIConfig, NRFIConfig, get_default_config
from ..utils.caching import disk_cache, read_parquet, write_parquet
from ..utils.logging import get_logger
from ..utils.rate_limit import global_limiter

log = get_logger(__name__)


# --- Lightweight DTOs ------------------------------------------------------

@dataclass
class GameStub:
    game_pk: int
    game_date: str            # YYYY-MM-DD
    season: int
    home_team: str            # tricode
    away_team: str
    venue_code: str
    venue_name: str
    first_pitch_ts: Optional[str]  # ISO UTC
    home_pitcher_id: Optional[int] = None
    away_pitcher_id: Optional[int] = None
    home_pitcher_hand: Optional[str] = None
    away_pitcher_hand: Optional[str] = None
    home_lineup: list[int] = field(default_factory=list)
    away_lineup: list[int] = field(default_factory=list)
    ump_id: Optional[int] = None
    ump_name: Optional[str] = None
    roof_status: Optional[str] = None

    def as_row(self) -> dict[str, Any]:
        return {
            "game_pk": self.game_pk,
            "game_date": self.game_date,
            "season": self.season,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "venue_code": self.venue_code,
            "venue_name": self.venue_name,
            "first_pitch_ts": self.first_pitch_ts,
            "roof_status": self.roof_status,
            "home_pitcher_id": self.home_pitcher_id,
            "away_pitcher_id": self.away_pitcher_id,
            "home_pitcher_hand": self.home_pitcher_hand,
            "away_pitcher_hand": self.away_pitcher_hand,
            "home_lineup": ",".join(str(x) for x in self.home_lineup),
            "away_lineup": ",".join(str(x) for x in self.away_lineup),
            "ump_id": self.ump_id,
        }


# Map MLB Stats API team abbreviations → our internal tricodes.
# Keep this here rather than in park_factors so we can adjust without
# touching the static park dictionary.
_TEAM_ABBR_FIX = {
    "AZ": "ARI",
    "CHW": "CWS",
    "WAS": "WSH",
    "SDG": "SD",
    "SFG": "SF",
    "TBR": "TB",
    "KCR": "KC",
}

# Map MLB Stats venue ID or name → our park tricode (best-effort).
# Includes alternate names from corporate renames (Rate Field 2026,
# Daikin Park 2025, Sutter Health Park 2025, Steinbrenner Field 2025).
# Update annually.
_VENUE_NAME_TO_CODE = {
    "Chase Field": "ARI", "Truist Park": "ATL", "Oriole Park at Camden Yards": "BAL",
    "Fenway Park": "BOS", "Wrigley Field": "CHC",
    "Guaranteed Rate Field": "CWS", "Rate Field": "CWS",  # 2026 rename
    "Great American Ball Park": "CIN", "Progressive Field": "CLE", "Coors Field": "COL",
    "Comerica Park": "DET",
    "Minute Maid Park": "HOU", "Daikin Park": "HOU",      # 2025 rename
    "Kauffman Stadium": "KC", "Angel Stadium": "LAA", "Dodger Stadium": "LAD",
    "loanDepot park": "MIA", "LoanDepot Park": "MIA",
    "American Family Field": "MIL", "Target Field": "MIN",
    "Citi Field": "NYM", "Yankee Stadium": "NYY",
    "Oakland Coliseum": "OAK", "Sutter Health Park": "OAK",   # 2025+ A's temporary home
    "Citizens Bank Park": "PHI", "PNC Park": "PIT", "Petco Park": "SD",
    "Oracle Park": "SF", "T-Mobile Park": "SEA", "Busch Stadium": "STL",
    "Tropicana Field": "TB",
    "George M. Steinbrenner Field": "TB", "Steinbrenner Field": "TB",  # 2025+ Rays temp
    "Globe Life Field": "TEX", "Rogers Centre": "TOR", "Nationals Park": "WSH",
}


def _normalize_team(abbr: str) -> str:
    return _TEAM_ABBR_FIX.get(abbr, abbr)


def _venue_to_code(venue_name: str) -> str:
    return _VENUE_NAME_TO_CODE.get(venue_name, venue_name[:3].upper())


# --- MLB Stats API client --------------------------------------------------

class MLBStatsClient:
    """Wraps statsapi.mlb.com endpoints we care about."""

    def __init__(self, api: APIConfig | None = None):
        self.api = api or APIConfig()
        self.limiter = global_limiter(self.api.requests_per_minute)
        self._http = httpx.Client(
            timeout=self.api.request_timeout_s,
            headers={"User-Agent": self.api.user_agent},
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.api.mlb_stats_api_base}{path}"
        with self.limiter.acquire():
            r = self._http.get(url, params=params or {})
            r.raise_for_status()
            return r.json()

    # ---- Schedule -------------------------------------------------------
    def schedule(self, target_date: str) -> list[GameStub]:
        """Return the list of games on `target_date` (YYYY-MM-DD)."""
        data = self._get("/schedule", {
            "sportId": 1,
            "date": target_date,
            "hydrate": "probablePitcher,linescore,team,venue,officials,weather",
        })
        out: list[GameStub] = []
        for day in data.get("dates", []):
            for g in day.get("games", []):
                venue_name = (g.get("venue") or {}).get("name", "")
                home = (g.get("teams") or {}).get("home", {}) or {}
                away = (g.get("teams") or {}).get("away", {}) or {}
                home_team = _normalize_team(((home.get("team") or {}).get("abbreviation") or "").upper())
                away_team = _normalize_team(((away.get("team") or {}).get("abbreviation") or "").upper())
                home_p = (home.get("probablePitcher") or {})
                away_p = (away.get("probablePitcher") or {})

                stub = GameStub(
                    game_pk=int(g["gamePk"]),
                    game_date=g.get("officialDate") or target_date,
                    season=int(g.get("season", target_date[:4])),
                    home_team=home_team,
                    away_team=away_team,
                    venue_code=_venue_to_code(venue_name),
                    venue_name=venue_name,
                    first_pitch_ts=g.get("gameDate"),
                    home_pitcher_id=home_p.get("id"),
                    away_pitcher_id=away_p.get("id"),
                )
                out.append(stub)
        return out

    # ---- People (pitcher hand etc) -------------------------------------
    def people(self, person_ids: Iterable[int]) -> dict[int, dict]:
        ids = [int(p) for p in person_ids if p]
        if not ids:
            return {}
        data = self._get("/people", {"personIds": ",".join(str(i) for i in ids)})
        return {int(p["id"]): p for p in data.get("people", [])}

    # ---- Boxscore (lineups + officials) ---------------------------------
    def boxscore(self, game_pk: int) -> dict:
        return self._get(f"/game/{game_pk}/boxscore")

    def linescore(self, game_pk: int) -> dict:
        return self._get(f"/game/{game_pk}/linescore")

    # ---- Hydrate a stub with lineups, ump, hand info --------------------
    def hydrate_stub(self, stub: GameStub) -> GameStub:
        try:
            box = self.boxscore(stub.game_pk)
        except httpx.HTTPError as e:
            log.warning("boxscore fetch failed (gamePk=%s): %s", stub.game_pk, e)
            return stub

        teams = box.get("teams", {})
        for side, attr_lineup in (("home", "home_lineup"), ("away", "away_lineup")):
            t = teams.get(side, {}) or {}
            order = t.get("battingOrder") or []
            lineup = [int(p) for p in order[:9]]
            setattr(stub, attr_lineup, lineup)

        # Home plate umpire
        for off in box.get("officials", []) or []:
            if (off.get("officialType") or "").lower().startswith("home plate"):
                stub.ump_id = (off.get("official") or {}).get("id")
                stub.ump_name = (off.get("official") or {}).get("fullName")
                break

        # Pitcher handedness (resolved via /people)
        ids = [stub.home_pitcher_id, stub.away_pitcher_id]
        people = self.people([i for i in ids if i])
        for side, p_id_attr, hand_attr in (
            ("home", "home_pitcher_id", "home_pitcher_hand"),
            ("away", "away_pitcher_id", "away_pitcher_hand"),
        ):
            pid = getattr(stub, p_id_attr)
            if pid and pid in people:
                hand = (people[pid].get("pitchHand") or {}).get("code")
                setattr(stub, hand_attr, hand)
        return stub


# --- pybaseball wrappers (lazy import) -------------------------------------

def _import_pybaseball():
    try:
        import pybaseball  # type: ignore
        # Statcast caching ourselves; turn off pybaseball's noisy progress.
        pybaseball.cache.enable()
        return pybaseball
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "pybaseball is required for Statcast pulls. "
            "Install via `pip install -r nrfi/requirements-nrfi.txt`."
        ) from e


def fetch_statcast_first_inning(
    start_date: str, end_date: str, *,
    config: NRFIConfig | None = None,
):
    """Return a DataFrame of pitch-level Statcast events for inning=1 only.

    Cached as parquet under <cache_dir>/parquet/statcast_first_inn/.
    Backtest replays should always pass the same date range to hit cache.
    """
    cfg = config or get_default_config()
    key = f"first_inn_{start_date}_{end_date}".replace("-", "")
    cached = read_parquet(cfg.cache_dir, "statcast_first_inn", key)
    if cached is not None:
        log.info("Statcast first-inn cache hit: %s", key)
        return cached

    pyb = _import_pybaseball()
    log.info("Pulling Statcast pitches %s..%s (this may take minutes)",
             start_date, end_date)
    df = pyb.statcast(start_dt=start_date, end_dt=end_date, verbose=False)
    if df is None or df.empty:
        return df
    df = df[df["inning"] == 1].copy()
    write_parquet(df, cfg.cache_dir, "statcast_first_inn", key)
    return df


def fetch_pitcher_season_stats(season: int, *, config: NRFIConfig | None = None):
    """Pitching leaderboard via pybaseball (FanGraphs)."""
    cfg = config or get_default_config()

    @disk_cache(cfg.cache_dir, ttl_seconds=24 * 3600,
                namespace=f"pitcher_leaderboard_{season}")
    def _inner():
        pyb = _import_pybaseball()
        return pyb.pitching_stats(season, qual=10)

    return _inner()


def fetch_batter_season_stats(season: int, *, config: NRFIConfig | None = None):
    cfg = config or get_default_config()

    @disk_cache(cfg.cache_dir, ttl_seconds=24 * 3600,
                namespace=f"batter_leaderboard_{season}")
    def _inner():
        pyb = _import_pybaseball()
        return pyb.batting_stats(season, qual=20)

    return _inner()


# --- Baseball Savant ABS (2026+) -------------------------------------------

def fetch_abs_umpire_table(season: int, *, config: NRFIConfig | None = None):
    """Return a DataFrame of per-umpire ABS challenge metrics.

    The Savant ABS dashboard exposes JSON behind the leaderboard table.
    Schema may evolve mid-season — we tolerate missing columns and
    surface only the fields we care about for downstream features:
    challenges_against, overturn_rate_against, called_strike_rate, etc.
    """
    cfg = config or get_default_config()

    @disk_cache(cfg.cache_dir, ttl_seconds=24 * 3600,
                namespace=f"savant_abs_{season}")
    def _inner():
        import pandas as pd
        api = APIConfig()
        url = f"{api.baseball_savant_abs}/leaderboard"
        params = {"type": "umpire", "season": season, "csv": "true"}
        with global_limiter(api.requests_per_minute).acquire():
            with httpx.Client(timeout=api.request_timeout_s,
                              headers={"User-Agent": api.user_agent}) as http:
                r = http.get(url, params=params)
                if r.status_code != 200:
                    log.warning("Savant ABS fetch failed: %s", r.status_code)
                    return pd.DataFrame()
                # The endpoint returns CSV when csv=true.
                from io import StringIO
                try:
                    return pd.read_csv(StringIO(r.text))
                except Exception as e:
                    log.warning("Could not parse Savant ABS CSV: %s", e)
                    return pd.DataFrame()

    return _inner()


# --- First-inning outcome reconstruction -----------------------------------

def first_inning_runs(linescore: dict) -> int:
    """Sum top+bottom 1st runs from a Stats API linescore payload."""
    runs = 0
    for inning in linescore.get("innings", []) or []:
        if inning.get("num") == 1:
            for side in ("home", "away"):
                runs += int((inning.get(side) or {}).get("runs", 0) or 0)
    return runs


# --- Top-level orchestrator (daily ETL) ------------------------------------

def daily_etl(target_date: str, store, *, config: NRFIConfig | None = None) -> int:
    """Fetch schedule + probable pitchers + lineups + ump → upsert into DB.

    Returns count of games persisted. Safe to re-run; uses INSERT OR REPLACE.
    """
    cfg = config or get_default_config()
    client = MLBStatsClient(cfg.api)
    try:
        stubs = client.schedule(target_date)
        log.info("MLB schedule %s: %d games", target_date, len(stubs))
        rows = []
        for s in stubs:
            client.hydrate_stub(s)
            rows.append(s.as_row())
        if rows:
            store.upsert("games", rows)
        return len(rows)
    finally:
        client.close()


def backfill_actuals(start_date: str, end_date: str, store, *,
                     config: NRFIConfig | None = None) -> int:
    """Walk the schedule day-by-day and persist first-inning outcomes."""
    cfg = config or get_default_config()
    client = MLBStatsClient(cfg.api)
    n = 0
    try:
        d0 = date.fromisoformat(start_date)
        d1 = date.fromisoformat(end_date)
        cur = d0
        from datetime import timedelta
        while cur <= d1:
            stubs = client.schedule(cur.isoformat())
            rows = []
            for s in stubs:
                try:
                    ls = client.linescore(s.game_pk)
                except httpx.HTTPError:
                    continue
                runs = first_inning_runs(ls)
                rows.append({
                    "game_pk": s.game_pk,
                    "first_inn_runs": runs,
                    "nrfi": runs == 0,
                })
            if rows:
                store.upsert("actuals", rows)
                n += len(rows)
            cur = cur + timedelta(days=1)
    finally:
        client.close()
    return n
