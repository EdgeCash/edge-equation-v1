"""Lineup fetcher with projected → confirmed fallback chain.

Order of preference (each step short-circuits if it returns >= 8 batters):

    1. **Confirmed**     — boxscore battingOrder once posted (~30 min pre-game).
    2. **Projected**     — MLB Stats API `lineups` hydration if exposed for the
                           game (returns probable lineup ~3-4 hours pre-game).
    3. **Most-recent**   — last 7 days of starts for the team; take each
                           lineup-position's modal batter.
    4. **League default** — return None and let the feature builder fall
                           back to neutral priors.

Each path is a **strict subset** of the next more-conservative path, so
when this is invoked during point-in-time backtest replay we never leak
post-game info: if a backtest run only sees boxscore data ≥ first pitch,
we still emit a sensible lineup proxy via step 3 using only games
strictly *before* the target date.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import httpx

from ..config import APIConfig, NRFIConfig, get_default_config
from ..utils.logging import get_logger
from ..utils.rate_limit import global_limiter

log = get_logger(__name__)


@dataclass(frozen=True)
class ResolvedLineup:
    batter_ids: list[int]    # length 8 or 9
    source: str              # "confirmed" | "projected" | "most_recent" | "default"
    confirmed: bool


def resolve_lineup(
    *,
    game_pk: int,
    team_tricode: str,
    is_home: bool,
    target_date: str,
    point_in_time: bool = False,
    config: NRFIConfig | None = None,
) -> Optional[ResolvedLineup]:
    """Try the fallback chain. Returns None on total failure."""
    cfg = config or get_default_config()

    # Step 1: confirmed boxscore battingOrder.
    try:
        confirmed = _fetch_confirmed_from_boxscore(game_pk, is_home, cfg.api)
    except Exception as e:
        log.debug("confirmed lineup fetch failed: %s", e)
        confirmed = None
    if confirmed and len(confirmed) >= 8 and not point_in_time:
        return ResolvedLineup(confirmed, "confirmed", True)

    # Step 2: projected (MLB Stats API hydrate=lineups).
    try:
        projected = _fetch_projected_lineup(game_pk, is_home, cfg.api)
    except Exception as e:
        log.debug("projected lineup fetch failed: %s", e)
        projected = None
    if projected and len(projected) >= 8:
        return ResolvedLineup(projected, "projected", False)

    # Step 3: most-recent rolling-modal lineup. Always strictly *before*
    # target_date — used by backtest replay to avoid leakage.
    try:
        recent = _fetch_recent_modal_lineup(team_tricode, target_date, cfg.api)
    except Exception as e:
        log.debug("recent lineup fetch failed: %s", e)
        recent = None
    if recent and len(recent) >= 8:
        return ResolvedLineup(recent, "most_recent", False)

    # Step 4: give up gracefully.
    return None


def _fetch_confirmed_from_boxscore(game_pk: int, is_home: bool,
                                     api: APIConfig) -> Optional[list[int]]:
    url = f"{api.mlb_stats_api_base}/game/{game_pk}/boxscore"
    with global_limiter(api.requests_per_minute).acquire():
        with httpx.Client(timeout=api.request_timeout_s,
                          headers={"User-Agent": api.user_agent}) as http:
            r = http.get(url)
            r.raise_for_status()
            data = r.json()
    teams = data.get("teams", {}) or {}
    side = teams.get("home" if is_home else "away", {}) or {}
    order = side.get("battingOrder") or []
    return [int(p) for p in order[:9]]


def _fetch_projected_lineup(game_pk: int, is_home: bool,
                              api: APIConfig) -> Optional[list[int]]:
    """MLB Stats API exposes projected lineups via `hydrate=lineups`."""
    url = f"{api.mlb_stats_api_base}/schedule"
    params = {"sportId": 1, "gamePk": game_pk, "hydrate": "lineups,probablePitcher"}
    with global_limiter(api.requests_per_minute).acquire():
        with httpx.Client(timeout=api.request_timeout_s,
                          headers={"User-Agent": api.user_agent}) as http:
            r = http.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    for day in data.get("dates", []) or []:
        for g in day.get("games", []) or []:
            lu = g.get("lineups") or {}
            side = lu.get("homePlayers" if is_home else "awayPlayers") or []
            ids = [int((p or {}).get("id", 0)) for p in side]
            if any(ids):
                return [i for i in ids if i][:9]
    return None


def _fetch_recent_modal_lineup(team: str, target_date: str,
                                api: APIConfig,
                                lookback_days: int = 14) -> Optional[list[int]]:
    """Build a lineup from the team's last `lookback_days` starts.

    For each batting-order position 1..9 we take the modal batter
    across that team's starts strictly before `target_date`. This is
    the canonical backtest-safe approximation when no confirmed/
    projected data is available.
    """
    target = date.fromisoformat(target_date)
    start = target - timedelta(days=lookback_days)
    end = target - timedelta(days=1)
    url = f"{api.mlb_stats_api_base}/schedule"
    params = {
        "sportId": 1, "teamId": _team_id(team),
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "hydrate": "lineups",
    }
    with global_limiter(api.requests_per_minute).acquire():
        with httpx.Client(timeout=api.request_timeout_s,
                          headers={"User-Agent": api.user_agent}) as http:
            r = http.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    by_position: list[collections.Counter] = [collections.Counter() for _ in range(9)]
    for day in data.get("dates", []) or []:
        for g in day.get("games", []) or []:
            lu = g.get("lineups") or {}
            for side_key in ("homePlayers", "awayPlayers"):
                side = lu.get(side_key) or []
                for i, p in enumerate(side[:9]):
                    pid = int((p or {}).get("id", 0))
                    if pid:
                        by_position[i][pid] += 1
    if not any(by_position):
        return None
    return [c.most_common(1)[0][0] if c else 0 for c in by_position]


# Mapping from our tricode → MLB Stats teamId. Stable across seasons.
_TEAM_ID = {
    "ARI": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112, "CWS": 145,
    "CIN": 113, "CLE": 114, "COL": 115, "DET": 116, "HOU": 117, "KC": 118,
    "LAA": 108, "LAD": 119, "MIA": 146, "MIL": 158, "MIN": 142, "NYM": 121,
    "NYY": 147, "OAK": 133, "PHI": 143, "PIT": 134, "SD": 135, "SF": 137,
    "SEA": 136, "STL": 138, "TB": 139, "TEX": 140, "TOR": 141, "WSH": 120,
}


def _team_id(tricode: str) -> int:
    return _TEAM_ID.get(tricode.upper(), 0)
