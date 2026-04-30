"""NCAAF games + plays loader via the College Football Data API.

Free tier: 1000 requests / month, plenty for a one-time backfill of
a single season (~135 D1 teams × 12 games = 800-ish games per
season, plus ~25k plays paginated). API key obtained at
https://collegefootballdata.com/key — set as the
``CFBD_API_KEY`` environment variable.

Endpoints we hit:

* ``GET /games`` — game-level schedule + final scores. One call per
  season.
* ``GET /plays`` — play-by-play. Paginated; one call per (season,
  week) pair. ~15 weeks × 1 call = 15 calls per season.
* ``GET /lines`` — historical betting lines from a few books that
  feed CFBD. Less complete than The Odds API but free.

Best-effort throughout: HTTP errors raise `LoaderError` so the
orchestrator checkpoints failed (date, op) pairs for retry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


CFBD_API_BASE = "https://api.collegefootballdata.com"
API_KEY_ENV_VAR = "CFBD_API_KEY"
DEFAULT_TIMEOUT_S = 60.0


class LoaderError(RuntimeError):
    """Raised when the loader can't return data — caller checkpoints
    the failure and retries on the next run."""


def _resolve_api_key(override: Optional[str]) -> str:
    key = override if override is not None else os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise LoaderError(
            f"CFBD API key not set. Provide api_key= or export "
            f"{API_KEY_ENV_VAR}. Free tier at https://collegefootballdata.com/key."
        )
    return key


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CfbdGamesResult:
    season: int
    n_games: int
    df: object


def fetch_cfbd_games(
    season: int, *,
    api_key: Optional[str] = None,
    http_client=None,
    season_type: str = "regular",
) -> CfbdGamesResult:
    """Pull the season's game schedule from College Football Data API."""
    key = _resolve_api_key(api_key)
    url = f"{CFBD_API_BASE}/games"
    params = {"year": int(season), "seasonType": season_type, "division": "fbs"}
    payload = _get_json(url, params=params, api_key=key, http_client=http_client)
    if not isinstance(payload, list):
        raise LoaderError(
            f"unexpected /games shape: {type(payload).__name__}",
        )
    df = _normalize_games_payload(payload, season=season)
    return CfbdGamesResult(season=season, n_games=int(len(df)), df=df)


@dataclass(frozen=True)
class CfbdPlaysResult:
    season: int
    week: int
    n_plays: int
    df: object


def fetch_cfbd_plays(
    season: int, *, week: int,
    api_key: Optional[str] = None,
    http_client=None,
    season_type: str = "regular",
) -> CfbdPlaysResult:
    """Pull one (season, week) of plays. Paginated automatically by
    the CFBD API; we ride a single call which returns the full week."""
    key = _resolve_api_key(api_key)
    url = f"{CFBD_API_BASE}/plays"
    params = {
        "year": int(season),
        "week": int(week),
        "seasonType": season_type,
    }
    payload = _get_json(url, params=params, api_key=key, http_client=http_client)
    if not isinstance(payload, list):
        raise LoaderError(
            f"unexpected /plays shape: {type(payload).__name__}",
        )
    df = _normalize_plays_payload(payload, season=season)
    return CfbdPlaysResult(
        season=season, week=week, n_plays=int(len(df)), df=df,
    )


@dataclass(frozen=True)
class CfbdLinesResult:
    season: int
    n_lines: int
    df: object


def fetch_cfbd_lines(
    season: int, *,
    api_key: Optional[str] = None,
    http_client=None,
) -> CfbdLinesResult:
    """Pull historical Spread / Total / ML lines for a season.

    CFBD's `/lines` endpoint covers a handful of books (Bovada,
    DraftKings, ESPN_BET, Caesars, etc.) — less complete than The
    Odds API historical, but free.
    """
    key = _resolve_api_key(api_key)
    url = f"{CFBD_API_BASE}/lines"
    params = {"year": int(season)}
    payload = _get_json(url, params=params, api_key=key, http_client=http_client)
    if not isinstance(payload, list):
        raise LoaderError(
            f"unexpected /lines shape: {type(payload).__name__}",
        )
    df = _normalize_lines_payload(payload, season=season)
    return CfbdLinesResult(season=season, n_lines=int(len(df)), df=df)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get_json(
    url: str, *, params: dict, api_key: str, http_client=None,
):
    """GET `url` with bearer-token auth and return parsed JSON."""
    owns_client = http_client is None
    if owns_client:
        try:
            import httpx
        except ImportError as e:  # pragma: no cover
            raise LoaderError(
                "httpx is required for CFBD fetch — install via "
                "`pip install -e .[nrfi]`",
            ) from e
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        try:
            resp = http_client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {api_key}",
                          "Accept": "application/json"},
            )
            resp.raise_for_status()
        except Exception as e:
            raise LoaderError(f"CFBD GET {url} failed: {e}") from e
        try:
            return resp.json()
        except Exception as e:
            raise LoaderError(f"CFBD JSON decode failed: {e}") from e
    finally:
        if owns_client:
            http_client.close()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize_games_payload(payload: Sequence[dict], *, season: int):
    """Map CFBD `/games` JSON to the football_games schema."""
    import pandas as pd
    if not payload:
        return pd.DataFrame()
    rows: list[dict] = []
    for g in payload:
        rows.append({
            "game_id": str(g.get("id", "")),
            "sport": "NCAAF",
            "season": int(season),
            "week": int(g.get("week") or 0),
            "season_type": str(g.get("season_type", "regular")).upper()[:4],
            "event_date": (g.get("start_date") or "")[:10],
            "kickoff_ts": g.get("start_date"),
            "home_team": str(g.get("home_team", "")),
            "away_team": str(g.get("away_team", "")),
            "home_tricode": str(g.get("home_team", ""))[:4].upper(),
            "away_tricode": str(g.get("away_team", ""))[:4].upper(),
            "venue": str(g.get("venue", "")),
            "venue_code": str(g.get("venue_id", "")),
            "is_dome": False,   # CFBD doesn't surface this; future hook
            "is_neutral_site": bool(g.get("neutral_site", False)),
        })
    return pd.DataFrame(rows)


def _normalize_plays_payload(payload: Sequence[dict], *, season: int):
    """Map CFBD `/plays` JSON to football_plays schema."""
    import pandas as pd
    if not payload:
        return pd.DataFrame()
    rows: list[dict] = []
    for p in payload:
        rows.append({
            "game_id": str(p.get("game_id", "")),
            "play_id": str(p.get("id", "")),
            "sport": "NCAAF",
            "quarter": int(p.get("period") or 0),
            "seconds_remaining": int(p.get("clock", {}).get(
                "seconds", 0,
            ) or 0) if isinstance(p.get("clock"), dict) else 0,
            "down": int(p.get("down") or 0),
            "yards_to_go": int(p.get("distance") or 0),
            "yardline": int(p.get("yard_line") or 0),
            "play_type": str(p.get("play_type", "")),
            "epa": float(p.get("ppa") or 0.0),  # CFBD names EPA "ppa"
            "success": bool(p.get("scoring", False)) or False,
            "home_wp": 0.5,                       # not surfaced by CFBD
            "rusher_id": "",
            "passer_id": "",
            "receiver_id": "",
        })
    return pd.DataFrame(rows)


def _normalize_lines_payload(payload: Sequence[dict], *, season: int):
    """Flatten CFBD `/lines` (one game with nested book quotes) into
    football_lines rows. Each game can carry multiple book quotes;
    each book × market becomes its own row."""
    import pandas as pd
    if not payload:
        return pd.DataFrame()
    rows: list[dict] = []
    captured = pd.Timestamp.now("UTC").isoformat(timespec="seconds")
    for g in payload:
        game_id = str(g.get("id", ""))
        for line in g.get("lines", []) or []:
            book = str(line.get("provider", "cfbd"))
            spread = line.get("spread")
            ou = line.get("overUnder")
            home_ml = line.get("homeMoneyline")
            away_ml = line.get("awayMoneyline")
            if spread is not None:
                rows.append({
                    "game_id": game_id, "market": "Spread", "side": "home",
                    "line_value": float(spread),
                    "american_odds": -110.0,    # CFBD doesn't surface juice
                    "book": book,
                    "line_captured_at": captured,
                    "is_closing": False,
                })
                rows.append({
                    "game_id": game_id, "market": "Spread", "side": "away",
                    "line_value": -float(spread),
                    "american_odds": -110.0,
                    "book": book,
                    "line_captured_at": captured,
                    "is_closing": False,
                })
            if ou is not None:
                rows.append({
                    "game_id": game_id, "market": "Total", "side": "over",
                    "line_value": float(ou),
                    "american_odds": -110.0,
                    "book": book,
                    "line_captured_at": captured,
                    "is_closing": False,
                })
                rows.append({
                    "game_id": game_id, "market": "Total", "side": "under",
                    "line_value": float(ou),
                    "american_odds": -110.0,
                    "book": book,
                    "line_captured_at": captured,
                    "is_closing": False,
                })
            if home_ml is not None:
                rows.append({
                    "game_id": game_id, "market": "ML", "side": "home",
                    "line_value": 0.0,
                    "american_odds": float(home_ml),
                    "book": book,
                    "line_captured_at": captured,
                    "is_closing": False,
                })
            if away_ml is not None:
                rows.append({
                    "game_id": game_id, "market": "ML", "side": "away",
                    "line_value": 0.0,
                    "american_odds": float(away_ml),
                    "book": book,
                    "line_captured_at": captured,
                    "is_closing": False,
                })
    return pd.DataFrame(rows)
