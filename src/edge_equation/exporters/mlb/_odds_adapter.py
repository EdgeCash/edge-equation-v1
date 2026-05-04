"""
MLB odds adapter — bridges v1's odds-fetching surface to the per-game
nested dict shape that clv_tracker.find_closing_price() expects.

Why a dedicated adapter (vs. reusing edge_equation.ingestion.odds_api_source)?

1. Output shape mismatch. TheOddsApiSource emits a flat list of
   {game_id, market_type, selection, line, odds, meta} rows. The CLV
   path needs them grouped by game with home/away keyed sub-dicts:

       {
         "source": "the-odds-api",
         "games": [
           {
             "home_team": "CHC", "away_team": "AZ",
             "moneyline": {"home": {decimal,american,book},
                           "away": {decimal,american,book}},
             "run_line":  [{"team": "home"|"away", "point": ±1.5,
                            "decimal", "american", "book"}, ...],
             "totals":    [{"point": 8.5,
                            "over":  {decimal,american,book},
                            "under": {decimal,american,book}}, ...],
           }, ...
         ]
       }

2. Cache semantics. Closing-line snapshots want a fresh fetch every 30
   minutes — caching is the bug, not the feature. v1's TheOddsApiClient
   is cache-first and would happily return stale 6-hour-old odds.

3. Surface stability. closing_snapshot.py was ported verbatim from
   scrapers and references `MLBOddsScraper(api_key=..., quota_log_path=...)`.
   Keeping that interface here means the verbatim port stays verbatim.

Env vars: prefers ODDS_API_KEY (scrapers convention) and falls back to
THE_ODDS_API_KEY (v1 convention) so workflow secrets named either way
just work.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from edge_equation.exporters.mlb.kelly import american_to_decimal


ENDPOINT = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
DEFAULT_REGIONS = "us"
DEFAULT_MARKETS = "h2h,spreads,totals"
DEFAULT_ODDS_FORMAT = "american"
DEFAULT_TIMEOUT_SECONDS = 15.0


def _resolve_api_key(override: Optional[str]) -> str:
    if override:
        return override
    for var in ("ODDS_API_KEY", "THE_ODDS_API_KEY"):
        value = os.environ.get(var)
        if value:
            return value
    raise RuntimeError(
        "Odds API key not set. Provide api_key= or export ODDS_API_KEY "
        "or THE_ODDS_API_KEY."
    )


def _select_bookmaker(bookmakers: list, preferred: Optional[str]) -> Optional[dict]:
    """First-preferred-then-first selection, mirroring TheOddsApiSource."""
    if not bookmakers:
        return None
    if preferred is not None:
        for b in bookmakers:
            if b.get("key") == preferred:
                return b
    return bookmakers[0]


def _parse_total_selection(name: str, point) -> tuple[str | None, float | None]:
    """The Odds API totals outcome carries name='Over'/'Under' and a
    numeric `point`. Older normalizations also embed the point in the
    name ("Over 8.5"); accept both for robustness."""
    if name is None:
        return None, None
    side_word = name.strip().lower()
    if " " in side_word:
        head, tail = side_word.split(None, 1)
        side_word = head
        if point is None:
            try:
                point = float(tail)
            except ValueError:
                point = None
    side_key = "over" if side_word == "over" else "under" if side_word == "under" else None
    try:
        point_val = float(point) if point is not None else None
    except (TypeError, ValueError):
        point_val = None
    return side_key, point_val


def _translate_game(raw_game: dict, preferred_bookmaker: Optional[str]) -> dict | None:
    """Convert one raw Odds API game into the nested CLV-friendly shape.

    Returns None if no bookmaker has any priced markets — the caller
    skips those rather than emitting an empty-shell game.
    """
    bookmaker = _select_bookmaker(raw_game.get("bookmakers", []), preferred_bookmaker)
    if bookmaker is None:
        return None
    book = bookmaker.get("key", "")
    home = raw_game.get("home_team")
    away = raw_game.get("away_team")
    out: dict = {
        "game_id": raw_game.get("id"),
        "commence_time": raw_game.get("commence_time"),
        "home_team": home,
        "away_team": away,
        "moneyline": {},
        "run_line": [],
        "totals": [],
    }
    for market in bookmaker.get("markets", []):
        key = market.get("key")
        for outcome in market.get("outcomes", []):
            american = outcome.get("price")
            if american is None:
                continue
            decimal = american_to_decimal(int(american))
            price_block = {
                "decimal": decimal,
                "american": int(american),
                "book": book,
            }
            if key == "h2h":
                team = outcome.get("name")
                side = "home" if team == home else "away" if team == away else None
                if side:
                    out["moneyline"][side] = price_block
            elif key == "spreads":
                team = outcome.get("name")
                side = "home" if team == home else "away" if team == away else None
                point = outcome.get("point")
                if side is None or point is None:
                    continue
                out["run_line"].append({
                    "team": side,
                    "point": float(point),
                    **price_block,
                })
            elif key == "totals":
                side_key, point = _parse_total_selection(
                    outcome.get("name"), outcome.get("point"),
                )
                if side_key is None or point is None:
                    continue
                offer = next(
                    (o for o in out["totals"] if abs(o["point"] - point) < 0.01),
                    None,
                )
                if offer is None:
                    offer = {"point": point}
                    out["totals"].append(offer)
                offer[side_key] = price_block
    if (
        not out["moneyline"]
        and not out["run_line"]
        and not out["totals"]
    ):
        return None
    return out


class MLBOddsScraper:
    """Drop-in shim with the surface scrapers' closing_snapshot.py expects.

    Constructor signature kept compatible:
        MLBOddsScraper(api_key=..., quota_log_path=...)

    fetch() returns:
        {"source": str, "games": [game_dict, ...]}
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        quota_log_path: Optional[Path] = None,
        regions: str = DEFAULT_REGIONS,
        markets: str = DEFAULT_MARKETS,
        preferred_bookmaker: Optional[str] = None,
        odds_format: str = DEFAULT_ODDS_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: Optional[httpx.Client] = None,
    ):
        self._api_key = api_key
        self.quota_log_path = Path(quota_log_path) if quota_log_path else None
        self.regions = regions
        self.markets = markets
        self.preferred_bookmaker = preferred_bookmaker
        self.odds_format = odds_format
        self.timeout = timeout
        self._http = http_client

    def _client(self) -> httpx.Client:
        if self._http is not None:
            return self._http
        return httpx.Client(timeout=self.timeout)

    def _record_quota(self, headers: httpx.Headers) -> None:
        """Mirror scrapers' quota tracking — append a minimal record so
        the daily card can surface remaining-credits headroom without
        re-hitting the API. Quota log shape matches scrapers':
            {"records": [{"at": iso, "remaining": int, "used": int}, ...]}
        """
        if not self.quota_log_path:
            return
        remaining = headers.get("x-requests-remaining")
        used = headers.get("x-requests-used")
        if remaining is None and used is None:
            return
        record = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "remaining": int(remaining) if remaining is not None else None,
            "used": int(used) if used is not None else None,
        }
        self.quota_log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(self.quota_log_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            payload = {"records": []}
        records = payload.setdefault("records", [])
        records.append(record)
        # Keep last 500 records — plenty for a season's worth of audit.
        if len(records) > 500:
            payload["records"] = records[-500:]
        tmp = self.quota_log_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.quota_log_path)

    def fetch(self) -> dict:
        api_key = _resolve_api_key(self._api_key)
        params = {
            "apiKey": api_key,
            "regions": self.regions,
            "markets": self.markets,
            "oddsFormat": self.odds_format,
        }
        client = self._client()
        try:
            response = client.get(ENDPOINT, params=params)
        finally:
            if self._http is None:
                client.close()
        response.raise_for_status()
        self._record_quota(response.headers)
        raw_games = response.json() or []
        translated = []
        for raw in raw_games:
            t = _translate_game(raw, self.preferred_bookmaker)
            if t is not None:
                translated.append(t)
        return {
            "source": "the-odds-api",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "games": translated,
        }
