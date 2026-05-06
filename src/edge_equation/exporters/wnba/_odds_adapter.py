"""
WNBA odds adapter — Odds API (basketball_wnba) → per-game nested dict.

Same shape and pattern as the MLB adapter
(src/edge_equation/exporters/mlb/_odds_adapter.py). For each priced
game returns:

    {
      "game_id": str,
      "commence_time": iso,
      "home_team": "<3-letter code>",
      "away_team": "<3-letter code>",
      "moneyline": {"home": {decimal,american,book},
                    "away": {decimal,american,book}},
      "spread":  [{"team": "home"|"away", "point": ±N.5,
                   "decimal","american","book"}, ...],
      "totals":  [{"point": 161.5,
                   "over":  {decimal,american,book},
                   "under": {decimal,american,book}}, ...],
    }

Env vars: ODDS_API_KEY (preferred) → THE_ODDS_API_KEY fallback.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx


ENDPOINT = "https://api.the-odds-api.com/v4/sports/basketball_wnba/odds"
DEFAULT_REGIONS = "us"
DEFAULT_MARKETS = "h2h,spreads,totals"
DEFAULT_ODDS_FORMAT = "american"
DEFAULT_TIMEOUT_SECONDS = 15.0


# Map Odds API full team names to the 3-letter codes the historical
# backfill / ESPN scrapers use.
TEAM_NAME_TO_CODE: dict[str, str] = {
    "Atlanta Dream": "ATL",
    "Chicago Sky": "CHI",
    "Connecticut Sun": "CONN",
    "Dallas Wings": "DAL",
    "Indiana Fever": "IND",
    "Las Vegas Aces": "LV",
    "Los Angeles Sparks": "LA",
    "Minnesota Lynx": "MIN",
    "New York Liberty": "NY",
    "Phoenix Mercury": "PHX",
    "Seattle Storm": "SEA",
    "Washington Mystics": "WAS",
    # Expansion
    "Golden State Valkyries": "GSV",
    "Toronto Tempo": "TOR",
    "Portland Fire": "POR",
}


def _team_code(name: str | None) -> str | None:
    if not name:
        return None
    if name in TEAM_NAME_TO_CODE:
        return TEAM_NAME_TO_CODE[name]
    if name in TEAM_NAME_TO_CODE.values():
        return name
    return None


def _resolve_api_key(override: Optional[str]) -> str:
    if override:
        return override
    for var in ("ODDS_API_KEY", "THE_ODDS_API_KEY"):
        v = os.environ.get(var)
        if v:
            return v
    raise RuntimeError(
        "Odds API key not set. Provide api_key= or export ODDS_API_KEY "
        "or THE_ODDS_API_KEY."
    )


def _select_bookmaker(bookmakers: list, preferred: Optional[str]) -> Optional[dict]:
    if not bookmakers:
        return None
    if preferred is not None:
        for b in bookmakers:
            if b.get("key") == preferred:
                return b
    return bookmakers[0]


def _american_to_decimal(am: float | int) -> float:
    am = float(am)
    if am > 0:
        return round(1 + am / 100, 4)
    if am < 0:
        return round(1 + 100 / -am, 4)
    return 1.0


def _parse_total_selection(name: str | None, point) -> tuple[str | None, float | None]:
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
    bookmaker = _select_bookmaker(raw_game.get("bookmakers", []), preferred_bookmaker)
    if bookmaker is None:
        return None
    book = bookmaker.get("key", "")
    home_full = raw_game.get("home_team")
    away_full = raw_game.get("away_team")
    home_code = _team_code(home_full)
    away_code = _team_code(away_full)
    if home_code is None or away_code is None:
        return None
    out: dict = {
        "game_id": raw_game.get("id"),
        "commence_time": raw_game.get("commence_time"),
        "home_team": home_code,
        "away_team": away_code,
        "moneyline": {},
        "spread": [],
        "totals": [],
    }
    for market in bookmaker.get("markets", []):
        key = market.get("key")
        for outcome in market.get("outcomes", []):
            american = outcome.get("price")
            if american is None:
                continue
            decimal = _american_to_decimal(int(american))
            price_block = {
                "decimal": decimal,
                "american": int(american),
                "book": book,
            }
            if key == "h2h":
                outcome_code = _team_code(outcome.get("name"))
                side = (
                    "home" if outcome_code == home_code
                    else "away" if outcome_code == away_code
                    else None
                )
                if side:
                    out["moneyline"][side] = price_block
            elif key == "spreads":
                outcome_code = _team_code(outcome.get("name"))
                side = (
                    "home" if outcome_code == home_code
                    else "away" if outcome_code == away_code
                    else None
                )
                point = outcome.get("point")
                if side is None or point is None:
                    continue
                out["spread"].append({
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
    if (not out["moneyline"] and not out["spread"] and not out["totals"]):
        return None
    return out


class WNBAOddsScraper:
    """Drop-in shim with the same surface as the MLB odds adapter."""

    @staticmethod
    def find_game(odds: dict, away: str, home: str) -> dict | None:
        if not odds:
            return None
        away_code = _team_code(away) or away
        home_code = _team_code(home) or home
        for g in odds.get("games", []) or []:
            if g.get("away_team") == away_code and g.get("home_team") == home_code:
                return g
        return None

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
        return self._http if self._http is not None else httpx.Client(timeout=self.timeout)

    def _record_quota(self, headers: httpx.Headers) -> None:
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
