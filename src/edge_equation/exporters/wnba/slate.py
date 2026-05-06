"""
WNBA today's slate fetcher — hits ESPN's free scoreboard JSON
directly. No inheritance from the NFL scraper (which v1 doesn't
have); this is a lean ~80-line standalone helper.

Used by the daily orchestrator to know which games are on tonight.
For each game returns the minimum the projector needs:
    {date, game_id, game_time, away_team, home_team}

ESPN scoreboard endpoint:
    https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard

Date param is `YYYYMMDD` (no dashes). Default fetches today (UTC).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import requests


SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
)
DEFAULT_TIMEOUT = 20.0


# ESPN abbreviations are mostly stable, but a couple deviate from
# common usage. Normalize to the codes the backfill files use.
TEAM_ALIASES = {
    "WSH": "WAS",   # Washington Mystics
    "GSV": "GSV",   # Golden State Valkyries (added 2025+)
    "VAL": "GSV",
}


def _canon_team(abbr: str | None) -> str | None:
    if not abbr:
        return None
    a = abbr.upper()
    return TEAM_ALIASES.get(a, a)


def fetch_slate(
    date: Optional[str] = None,
    http_get=None,
) -> list[dict]:
    """Return list of {date, game_id, game_time, away_team, home_team}.

    date: YYYY-MM-DD; defaults to today (UTC).
    http_get: injectable for testing; defaults to requests.get.
    """
    if http_get is None:
        http_get = requests.get
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    espn_date = date.replace("-", "")
    params = {"dates": espn_date, "limit": 50}
    try:
        resp = http_get(SCOREBOARD_URL, params=params, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException:
        return []

    events = payload.get("events") or []
    out: list[dict] = []
    for ev in events:
        comp_list = ev.get("competitions") or []
        if not comp_list:
            continue
        comp = comp_list[0]
        competitors = comp.get("competitors") or []
        away = next(
            (c for c in competitors if c.get("homeAway") == "away"), None,
        )
        home = next(
            (c for c in competitors if c.get("homeAway") == "home"), None,
        )
        if not (away and home):
            continue
        away_abbr = _canon_team(((away.get("team") or {}).get("abbreviation")))
        home_abbr = _canon_team(((home.get("team") or {}).get("abbreviation")))
        if not (away_abbr and home_abbr):
            continue

        out.append({
            "date": date,
            "game_id": str(ev.get("id") or ""),
            "game_time": ev.get("date") or "",   # ISO-8601 from ESPN
            "away_team": away_abbr,
            "home_team": home_abbr,
            "venue": ((comp.get("venue") or {}).get("fullName") or ""),
        })
    return out
