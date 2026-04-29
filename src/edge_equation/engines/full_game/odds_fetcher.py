"""Fetch + normalize MLB full-game market lines from The Odds API.

Two fetch paths:

* `fetch_event_list` — standard `/sports/{sport}/odds` endpoint with
  the cheap markets (h2h / spreads / totals). 1 credit total. Returns
  events + their standard-market lines in a single call so we don't
  hit the per-event endpoint when the operator only wants the standard
  trio.
* `fetch_event_full_game_props` — per-event endpoint for alternate
  markets (`totals_1st_5_innings`, `h2h_1st_5_innings`,
  `alternate_team_totals`). ~3 credits per event call.

Best-effort: any error path returns the empty form so the daily email
keeps working when the Odds API is rate-limited / down / paywalled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from edge_equation.utils.kelly import american_to_decimal
from edge_equation.utils.logging import get_logger

# Reuse the NRFI engine's team-name→tricode mapping so all engines
# speak one team-id dialect. The NRFI module is already imported in
# the daily flow, so this isn't a new dependency cost.
from edge_equation.engines.nrfi.data.odds import _MLB_FULL_NAME_TO_TRICODE

from .markets import (
    ALL_MARKETS_PARAM, MLB_FULL_GAME_MARKETS,
    STANDARD_MARKETS_PARAM, FullGameMarket,
    market_for_odds_api_key,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider config (matches NRFI/Props convention)
# ---------------------------------------------------------------------------

THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"
SPORT_KEY_MLB = "baseball_mlb"
DEFAULT_REGIONS = "us"
DEFAULT_TIMEOUT_S = 30.0
API_KEY_ENV_VAR = "THE_ODDS_API_KEY"

PREFERRED_BOOK_KEYS: tuple[str, ...] = (
    "draftkings", "fanduel", "betmgm", "caesars",
    "pointsbetus", "williamhill_us",
)


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FullGameLine:
    """One side of one full-game market quote.

    For team-side markets (ML, Run_Line, F5_ML, Team_Total) the
    `side` carries the team tricode ('NYY', 'BOS') AND a normalized
    direction marker (`Over`/`Under` for team totals). For
    over/under markets (Total, F5_Total) `side` is the literal
    `Over`/`Under` and `team_tricode` is empty.
    """
    event_id: str
    home_team: str            # full name from API
    away_team: str            # full name from API
    home_tricode: str         # canonical 3-letter code (or "" if unknown)
    away_tricode: str
    commence_time: str
    market: FullGameMarket
    side: str                 # 'Over' | 'Under' | tricode | f"{tricode} Over"
    line_value: Optional[float]  # the spread / total number; None for ML
    american_odds: float
    decimal_odds: float
    book: str
    team_tricode: str = ""    # for team-side markets, the staked team


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
    markets: str = STANDARD_MARKETS_PARAM,
) -> list[dict]:
    """Pull today's events with standard-market lines (1 credit)."""
    api_key = _resolve_api_key(api_key)
    url = f"{THE_ODDS_API_BASE}/{sport_key}/odds"
    owns_client = http_client is None
    if owns_client:
        import httpx
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        resp = http_client.get(url, params={
            "apiKey": api_key, "regions": regions,
            "markets": markets, "oddsFormat": "american",
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


def fetch_event_full_game_props(
    *,
    event_id: str,
    sport_key: str = SPORT_KEY_MLB,
    markets_param: Optional[str] = None,
    api_key: Optional[str] = None,
    http_client=None,
    regions: str = DEFAULT_REGIONS,
) -> dict:
    """Fetch alternate-market full-game odds for one event.

    Use this only for the alt markets (F5_Total, F5_ML, Team_Total).
    Standard markets are already in the event list. Costs `regions ×
    markets` credits per call.
    """
    api_key = _resolve_api_key(api_key)
    if markets_param is None:
        # Default to the alt-only set so we don't double-fetch standard
        # markets that the event list already returned.
        alt_keys = ",".join(
            m.odds_api_key for m in MLB_FULL_GAME_MARKETS.values()
            if m.requires_alternate
        )
        markets_param = alt_keys

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
    if not bookmakers:
        return None
    by_key = {b.get("key", ""): b for b in bookmakers}
    for preferred in PREFERRED_BOOK_KEYS:
        if preferred in by_key:
            return by_key[preferred]
    return bookmakers[0]


def _name_to_tricode(name: str) -> str:
    return _MLB_FULL_NAME_TO_TRICODE.get(name, "")


def normalize_event_payload(
    payload: dict, *,
    canonical_filter: Optional[set[str]] = None,
) -> list[FullGameLine]:
    """Walk a per-event payload and emit one `FullGameLine` per side
    of every supported market the chosen bookmaker posted.

    `canonical_filter` lets the caller drop markets it didn't ask for
    (e.g., when normalising a standard-only response).
    """
    book = _select_book(payload.get("bookmakers", []))
    if book is None:
        return []
    event_id = payload.get("id", "")
    home = payload.get("home_team", "")
    away = payload.get("away_team", "")
    home_tri = _name_to_tricode(home)
    away_tri = _name_to_tricode(away)
    commence = payload.get("commence_time", "")
    book_key = book.get("key", "")

    out: list[FullGameLine] = []
    for market in book.get("markets", []):
        api_key = market.get("key", "")
        canonical = market_for_odds_api_key(api_key)
        if canonical is None:
            continue
        if canonical_filter is not None and canonical.canonical not in canonical_filter:
            continue

        for outcome in market.get("outcomes", []):
            price = outcome.get("price")
            if price is None:
                continue
            try:
                amer = float(price)
            except (TypeError, ValueError):
                continue
            point_raw = outcome.get("point")
            line_value: Optional[float] = None
            if point_raw is not None:
                try:
                    line_value = float(point_raw)
                except (TypeError, ValueError):
                    line_value = None

            name = outcome.get("name", "")
            description = outcome.get("description", "") or ""

            # Side / team-tricode disambiguation per market.
            if canonical.side_kind == "over_under":
                # Total / F5_Total — name is "Over" / "Under".
                # Team_Total surfaces with `description` = team name
                # plus name = "Over"/"Under".
                team_tricode = _name_to_tricode(description) if description else ""
                side = name
            else:
                # ML / Run_Line / F5_ML — name carries the team's
                # full name. Convert to tricode for stable keying.
                team_tricode = _name_to_tricode(name)
                side = team_tricode or name

            out.append(FullGameLine(
                event_id=event_id, home_team=home, away_team=away,
                home_tricode=home_tri, away_tricode=away_tri,
                commence_time=commence, market=canonical,
                side=str(side), line_value=line_value,
                american_odds=amer,
                decimal_odds=american_to_decimal(amer),
                book=book_key, team_tricode=team_tricode,
            ))
    return out


def fetch_all_full_game_lines(
    *,
    target_date: str,
    sport_key: str = SPORT_KEY_MLB,
    api_key: Optional[str] = None,
    http_client=None,
    include_alternates: bool = False,
) -> list[FullGameLine]:
    """One-shot: pull today's events + standard-market lines, optionally
    fetch alt markets per-event when `include_alternates=True`.

    Returns a flat list of `FullGameLine` rows for events whose
    `commence_time` falls on `target_date` (UTC). Best-effort throughout.
    """
    try:
        events = fetch_event_list(
            sport_key=sport_key, api_key=api_key, http_client=http_client,
        )
    except Exception as e:
        log.warning("event-list fetch failed (%s): %s",
                      type(e).__name__, e)
        return []

    out: list[FullGameLine] = []
    standard_canonical = {
        m.canonical for m in MLB_FULL_GAME_MARKETS.values()
        if not m.requires_alternate
    }
    alternate_canonical = {
        m.canonical for m in MLB_FULL_GAME_MARKETS.values()
        if m.requires_alternate
    }

    owns_client = http_client is None
    if owns_client:
        import httpx
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        for event in events:
            commence = event.get("commence_time", "")
            if commence[:10] != target_date:
                continue
            # Standard markets already in the event payload.
            out.extend(normalize_event_payload(
                event, canonical_filter=standard_canonical,
            ))
            if not include_alternates:
                continue
            try:
                alt_payload = fetch_event_full_game_props(
                    event_id=event["id"], sport_key=sport_key,
                    api_key=api_key, http_client=http_client,
                )
            except Exception as e:
                log.warning("event %s alt fetch failed (%s): %s",
                              event.get("id"), type(e).__name__, e)
                continue
            out.extend(normalize_event_payload(
                alt_payload, canonical_filter=alternate_canonical,
            ))
    finally:
        if owns_client:
            http_client.close()
    return out
