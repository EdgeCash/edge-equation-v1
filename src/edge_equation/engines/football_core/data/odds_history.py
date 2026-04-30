"""The Odds API historical-lines fetcher — paid-tier only.

The Odds API's `/v4/historical/sports/{sport}/odds` endpoint returns
historical bookmaker quotes for a specific date+time. **It requires
an upgraded plan**; the free / Tier-1 plans return only current
odds. The orchestrator gates this loader behind a
`include_historical_odds=True` flag so operators on the free plan
don't burn fail-fast credits trying to use it.

Endpoint
~~~~~~~~

``GET /v4/historical/sports/{sport_key}/odds?date=YYYY-MM-DDTHH:MM:SSZ``

Returns the quote-set as it existed at that timestamp. We pick a
snapshot at kickoff-minus-5min so we get the closest-to-closing line.

Set ``THE_ODDS_API_KEY`` in env (same secret name as the MLB engines).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


THE_ODDS_API_HISTORICAL = (
    "https://api.the-odds-api.com/v4/historical/sports/{sport_key}/odds"
)
API_KEY_ENV_VAR = "THE_ODDS_API_KEY"
DEFAULT_TIMEOUT_S = 30.0


class LoaderError(RuntimeError):
    """Raised when the loader can't return data."""


def _resolve_api_key(override: Optional[str]) -> str:
    key = override if override is not None else os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise LoaderError(
            f"Odds API key not set. Provide api_key= or export "
            f"{API_KEY_ENV_VAR}.",
        )
    return key


@dataclass(frozen=True)
class HistoricalOddsResult:
    sport_key: str
    target_iso: str
    n_lines: int
    df: object   # pandas DataFrame ready for football_lines upsert


def fetch_historical_lines(
    *, sport_key: str, target_iso: str,
    markets: str = "spreads,totals,h2h",
    regions: str = "us",
    api_key: Optional[str] = None,
    http_client=None,
) -> HistoricalOddsResult:
    """Pull the Odds API historical snapshot at `target_iso`.

    `target_iso` should be a kickoff-anchored timestamp like
    `"2025-09-07T17:25:00Z"`. The Odds API returns the snapshot
    closest to that time.
    """
    key = _resolve_api_key(api_key)
    url = THE_ODDS_API_HISTORICAL.format(sport_key=sport_key)
    params = {
        "apiKey": key, "date": target_iso,
        "regions": regions, "markets": markets,
        "oddsFormat": "american",
    }

    owns_client = http_client is None
    if owns_client:
        try:
            import httpx
        except ImportError as e:  # pragma: no cover
            raise LoaderError(
                "httpx is required for Odds API historical fetch",
            ) from e
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        try:
            resp = http_client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            raise LoaderError(f"Odds API historical fetch failed: {e}") from e
    finally:
        if owns_client:
            http_client.close()

    df = _normalize_historical_payload(payload)
    return HistoricalOddsResult(
        sport_key=sport_key, target_iso=target_iso,
        n_lines=int(len(df)), df=df,
    )


def _normalize_historical_payload(payload):
    """Flatten the Odds API historical response into football_lines rows.

    Response shape::

        {
          "timestamp": "...",
          "data": [
            {"id": "evt1", "home_team": "...", "away_team": "...",
             "bookmakers": [
               {"key": "draftkings", "markets": [
                 {"key": "spreads", "outcomes": [
                   {"name": "...", "point": -3.5, "price": -110}, ...
                 ]}, ...
               ]}, ...
             ]
            }, ...
          ]
        }
    """
    import pandas as pd
    if not isinstance(payload, dict):
        return pd.DataFrame()
    captured = payload.get("timestamp", "")
    rows: list[dict] = []
    for ev in payload.get("data", []) or []:
        game_id = str(ev.get("id", ""))
        for bm in ev.get("bookmakers", []) or []:
            book = str(bm.get("key", ""))
            for mk in bm.get("markets", []) or []:
                market_canonical = _market_canonical(mk.get("key", ""))
                if market_canonical is None:
                    continue
                for oc in mk.get("outcomes", []) or []:
                    side = _side_for(market_canonical, oc, ev)
                    if side is None:
                        continue
                    rows.append({
                        "game_id": game_id,
                        "market": market_canonical,
                        "side": side,
                        "line_value": float(oc.get("point") or 0.0),
                        "american_odds": float(oc.get("price") or -110.0),
                        "book": book,
                        "line_captured_at": captured,
                        "is_closing": False,
                    })
    return pd.DataFrame(rows)


def _market_canonical(odds_api_key: str) -> Optional[str]:
    """Map Odds API market key → our canonical name."""
    return {
        "spreads": "Spread",
        "totals": "Total",
        "h2h": "ML",
    }.get(odds_api_key)


def _side_for(market: str, outcome: dict, event: dict) -> Optional[str]:
    """Resolve the side label for one bookmaker outcome."""
    name = str(outcome.get("name", ""))
    if market == "Total":
        n = name.lower()
        if "over" in n:
            return "over"
        if "under" in n:
            return "under"
        return None
    home = str(event.get("home_team", ""))
    if name == home:
        return "home"
    return "away"
