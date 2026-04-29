"""Fetch + normalize MLB player-prop market lines from The Odds API.

The Odds API exposes alternate markets only via the per-event endpoint
``/sports/{sport}/events/{event_id}/odds``. Each call costs roughly
3 credits per event (regions × markets factor). This module is the
single boundary between the props engine and that endpoint:

* `fetch_event_list` — get today's MLB events (1 credit total).
* `fetch_event_player_props` — get player-prop lines for one event
  (~3 credits).
* `normalize_event_payload` — turn the raw bookmaker JSON into typed
  `PlayerPropLine` rows.

Best-effort throughout — every error path returns the empty form so a
quota cap or transient 5xx degrades gracefully rather than crashing
the daily email.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from edge_equation.utils.kelly import american_to_decimal
from edge_equation.utils.logging import get_logger

from .markets import MLB_PROP_MARKETS, PropMarket, market_for_odds_api_key

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider config — reuses the NRFI engine's THE_ODDS_API_KEY env var
# ---------------------------------------------------------------------------

THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"
SPORT_KEY_MLB = "baseball_mlb"
DEFAULT_REGIONS = "us"
DEFAULT_TIMEOUT_S = 30.0
API_KEY_ENV_VAR = "THE_ODDS_API_KEY"

# Same priority order the NRFI engine uses, for snapshot consistency.
PREFERRED_BOOK_KEYS: tuple[str, ...] = (
    "draftkings", "fanduel", "betmgm", "caesars",
    "pointsbetus", "williamhill_us",
)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerPropLine:
    """One side (Over/Under) of one player prop quote."""
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    market: PropMarket
    player_name: str
    side: str           # 'Over' / 'Under' / 'Yes' / 'No'
    line_value: float   # the prop number (e.g. 0.5 for HR, 5.5 for Ks)
    american_odds: float
    decimal_odds: float
    book: str


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _resolve_api_key(override: Optional[str]) -> str:
    key = override if override is not None else os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise RuntimeError(
            f"Odds API key not set. Provide api_key= or export {API_KEY_ENV_VAR}.",
        )
    return key


def fetch_event_list(
    *,
    sport_key: str = SPORT_KEY_MLB,
    api_key: Optional[str] = None,
    http_client=None,
    regions: str = DEFAULT_REGIONS,
) -> list[dict]:
    """List today's events (id / teams / commence_time) for `sport_key`.

    Costs 1 credit. We hit `markets=h2h` (cheapest valid market) since
    the standard endpoint doesn't return alternate markets anyway and
    we only need the event_ids.
    """
    api_key = _resolve_api_key(api_key)
    url = f"{THE_ODDS_API_BASE}/{sport_key}/odds"
    owns_client = http_client is None
    if owns_client:
        import httpx
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        resp = http_client.get(url, params={
            "apiKey": api_key, "regions": regions,
            "markets": "h2h", "oddsFormat": "american",
            "dateFormat": "iso",
        })
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError(
                f"Unexpected events list shape: {type(data).__name__}",
            )
        return data
    finally:
        if owns_client:
            http_client.close()


def fetch_event_player_props(
    *,
    event_id: str,
    sport_key: str = SPORT_KEY_MLB,
    markets_param: Optional[str] = None,
    api_key: Optional[str] = None,
    http_client=None,
    regions: str = DEFAULT_REGIONS,
) -> dict:
    """Fetch player-prop alternate markets for a single event.

    `markets_param` defaults to the joined keys of every market in
    MLB_PROP_MARKETS. Cost is `regions × markets` credits per call.
    """
    api_key = _resolve_api_key(api_key)
    if markets_param is None:
        from .markets import ODDS_API_MARKETS_PARAM
        markets_param = ODDS_API_MARKETS_PARAM

    url = f"{THE_ODDS_API_BASE}/{sport_key}/events/{event_id}/odds"
    owns_client = http_client is None
    if owns_client:
        import httpx
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        resp = http_client.get(url, params={
            "apiKey": api_key, "regions": regions,
            "markets": markets_param, "oddsFormat": "american",
            "dateFormat": "iso",
        })
        resp.raise_for_status()
        return resp.json()
    finally:
        if owns_client:
            http_client.close()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _select_book(bookmakers: Sequence[dict]) -> Optional[dict]:
    """Pick the highest-priority book that posted this market."""
    if not bookmakers:
        return None
    by_key = {b.get("key", ""): b for b in bookmakers}
    for preferred in PREFERRED_BOOK_KEYS:
        if preferred in by_key:
            return by_key[preferred]
    return bookmakers[0]


def normalize_event_payload(payload: dict) -> list[PlayerPropLine]:
    """Walk a per-event payload and emit one `PlayerPropLine` per
    (player, market, side) row from the chosen bookmaker.

    The Odds API's per-event response carries `description=<player_name>`
    on each outcome for player-prop markets. The `name` field is the
    side label (Over / Under) and `point` is the prop number.
    """
    book = _select_book(payload.get("bookmakers", []))
    if book is None:
        return []
    event_id = payload.get("id", "")
    home = payload.get("home_team", "")
    away = payload.get("away_team", "")
    commence = payload.get("commence_time", "")
    book_key = book.get("key", "")

    out: list[PlayerPropLine] = []
    for market in book.get("markets", []):
        api_key = market.get("key", "")
        canonical = market_for_odds_api_key(api_key)
        if canonical is None:
            continue
        for outcome in market.get("outcomes", []):
            point = outcome.get("point")
            price = outcome.get("price")
            player = outcome.get("description") or outcome.get("name", "")
            side = outcome.get("name", "")
            if point is None or price is None or not player:
                continue
            try:
                line_value = float(point)
                amer = float(price)
            except (TypeError, ValueError):
                continue
            out.append(PlayerPropLine(
                event_id=event_id, home_team=home, away_team=away,
                commence_time=commence, market=canonical,
                player_name=str(player), side=str(side),
                line_value=line_value, american_odds=amer,
                decimal_odds=american_to_decimal(amer),
                book=book_key,
            ))
    return out


def fetch_all_player_props(
    *,
    target_date: str,
    sport_key: str = SPORT_KEY_MLB,
    api_key: Optional[str] = None,
    http_client=None,
) -> list[PlayerPropLine]:
    """One-shot helper: pull today's events + per-event prop lines.

    Returns a flat list of `PlayerPropLine` rows for every event whose
    `commence_time` falls on `target_date` (UTC). Best-effort — events
    that error out individually are skipped, not raised.
    """
    try:
        events = fetch_event_list(
            sport_key=sport_key, api_key=api_key, http_client=http_client,
        )
    except Exception as e:
        log.warning("event-list fetch failed (%s): %s",
                      type(e).__name__, e)
        return []

    out: list[PlayerPropLine] = []
    owns_client = http_client is None
    if owns_client:
        import httpx
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        for event in events:
            commence = event.get("commence_time", "")
            if commence[:10] != target_date:
                continue
            try:
                payload = fetch_event_player_props(
                    event_id=event["id"], sport_key=sport_key,
                    api_key=api_key, http_client=http_client,
                )
            except Exception as e:
                log.warning("event %s fetch failed (%s): %s",
                              event.get("id"), type(e).__name__, e)
                continue
            out.extend(normalize_event_payload(payload))
    finally:
        if owns_client:
            http_client.close()
    return out
