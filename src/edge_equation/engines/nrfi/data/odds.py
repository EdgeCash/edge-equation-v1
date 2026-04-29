"""NRFI/YRFI live closing-line capture.

Phase 4 (closing-line only). Once per day — typically ~30 min before
the earliest game's first pitch — we hit The Odds API per-event
endpoint with `markets=totals_1st_1_innings` and persist a single
"closing-line" snapshot per game. The settlement pass then reads from
`nrfi_odds_snapshot` to use the actual line we'd have bet at, falling
back to the existing -120 / -105 defaults when no quote was captured.

Why per-event, not the standard /odds endpoint?
    Inning markets are alternates; The Odds API only returns them via
    /v4/sports/{sport}/events/{event_id}/odds. Each per-event call
    counts as 3 credits (regions × markets factor). For a 15-game
    slate that's ~45 credits/day = ~1,350/mo on top of existing usage,
    well within a Tier 1 ($30/mo) plan's 20K credit budget.

Why one fetch per day, not a continuous closing-line capture?
    Books usually post 1st-inning lines a few hours before first pitch
    and rarely move them more than a few cents. A single fetch ~30 min
    before earliest first-pitch is the standard "closing-line proxy"
    that pays off the cost without blowing the credit budget. A future
    PR can swap to multi-fetch once the user upgrades plans.

Provider abstraction
    Currently The Odds API only. Module-level constants control the
    provider so a future migration to OddsJam / SportsGameOdds is one
    file change instead of a refactor.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from edge_equation.utils.kelly import american_to_decimal
from edge_equation.utils.logging import get_logger

from ..config import NRFIConfig, get_default_config
from .storage import NRFIStore

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"
SPORT_KEY_MLB = "baseball_mlb"
INNING_TOTALS_MARKET = "totals_1st_1_innings"
DEFAULT_REGIONS = "us"
DEFAULT_TIMEOUT_S = 30.0
API_KEY_ENV_VAR = "THE_ODDS_API_KEY"

# Books we prefer for the captured snapshot, in priority order. The
# first book in this list that posts a 1st-inning total wins. Falling
# back to "first available" is the last resort. Most US bettors live on
# DraftKings or FanDuel so leading with those keeps the captured odds
# close to what the operator would actually have bet.
PREFERRED_BOOK_KEYS: tuple[str, ...] = (
    "draftkings",
    "fanduel",
    "betmgm",
    "caesars",
    "pointsbetus",
    "williamhill_us",  # Caesars's old key — keep for historical robustness
)


# ---------------------------------------------------------------------------
# MLB full-name → tricode (Odds API team-name dialect)
# ---------------------------------------------------------------------------

# The Odds API uses the franchise's full team name. Map back to the
# MLB Stats API tricodes the rest of the engine speaks.
_MLB_FULL_NAME_TO_TRICODE: dict[str, str] = {
    "Arizona Diamondbacks":  "ARI",
    "Atlanta Braves":        "ATL",
    "Baltimore Orioles":     "BAL",
    "Boston Red Sox":        "BOS",
    "Chicago Cubs":          "CHC",
    "Chicago White Sox":     "CWS",
    "Cincinnati Reds":       "CIN",
    "Cleveland Guardians":   "CLE",
    "Colorado Rockies":      "COL",
    "Detroit Tigers":        "DET",
    "Houston Astros":        "HOU",
    "Kansas City Royals":    "KC",
    "Los Angeles Angels":    "LAA",
    "Los Angeles Dodgers":   "LAD",
    "Miami Marlins":         "MIA",
    "Milwaukee Brewers":     "MIL",
    "Minnesota Twins":       "MIN",
    "New York Mets":         "NYM",
    "New York Yankees":      "NYY",
    "Oakland Athletics":     "OAK",
    "Athletics":             "OAK",  # 2025+ rename
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates":    "PIT",
    "San Diego Padres":      "SD",
    "San Francisco Giants":  "SF",
    "Seattle Mariners":      "SEA",
    "St. Louis Cardinals":   "STL",
    "Tampa Bay Rays":        "TB",
    "Texas Rangers":         "TEX",
    "Toronto Blue Jays":     "TOR",
    "Washington Nationals":  "WSH",
}


def _full_name_to_tricode(name: str) -> Optional[str]:
    """Map a full team name to its MLB Stats API tricode.

    Returns None when the name doesn't match — caller should skip
    the event rather than mis-attribute it.
    """
    return _MLB_FULL_NAME_TO_TRICODE.get(name)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OddsSnapshot:
    """One captured (game, market, book) odds quote."""
    game_pk: int
    market_type: str       # 'NRFI' or 'YRFI'
    american_odds: float
    decimal_odds: float
    book: str
    captured_at: datetime
    snapshot_kind: str     # 'closing'


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


_DDL_ODDS_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS nrfi_odds_snapshot (
    game_pk        BIGINT,
    market_type    VARCHAR,
    snapshot_kind  VARCHAR,
    american_odds  DOUBLE,
    decimal_odds   DOUBLE,
    book           VARCHAR,
    captured_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (game_pk, market_type, snapshot_kind)
)
"""


def init_odds_tables(store: NRFIStore) -> None:
    """Idempotent DDL — safe to call on every capture."""
    store.execute(_DDL_ODDS_SNAPSHOT)


# ---------------------------------------------------------------------------
# HTTP helpers (httpx — matches the rest of the engine's HTTP surface)
# ---------------------------------------------------------------------------


def _resolve_api_key(override: Optional[str]) -> str:
    key = override if override is not None else os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise RuntimeError(
            f"Odds API key not set. Provide api_key= or export {API_KEY_ENV_VAR}."
        )
    return key


def _fetch_event_list(
    *,
    sport_key: str,
    api_key: str,
    http_client,
    regions: str = DEFAULT_REGIONS,
) -> list[dict]:
    """List today's events from the standard /sports/{sport}/odds endpoint.

    Returns the raw JSON array — each entry has 'id', 'home_team',
    'away_team', 'commence_time'.

    The standard endpoint costs 1 credit and is necessary because the
    per-event endpoint requires the event_id we don't otherwise have.
    """
    url = f"{THE_ODDS_API_BASE}/{sport_key}/odds"
    resp = http_client.get(url, params={
        "apiKey": api_key,
        "regions": regions,
        "markets": "h2h",       # Cheapest valid market — we just need the IDs.
        "oddsFormat": "american",
        "dateFormat": "iso",
    })
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected events list shape: {type(data).__name__}")
    return data


def _fetch_event_odds(
    *,
    sport_key: str,
    event_id: str,
    api_key: str,
    http_client,
    markets: str = INNING_TOTALS_MARKET,
    regions: str = DEFAULT_REGIONS,
) -> dict:
    """Fetch alternate-market odds for a single event.

    Returns the per-event JSON object with bookmakers/markets. Costs
    `regions × markets` credits per call (typically 3 per game on a
    single-region single-market call like ours).
    """
    url = f"{THE_ODDS_API_BASE}/{sport_key}/events/{event_id}/odds"
    resp = http_client.get(url, params={
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "american",
        "dateFormat": "iso",
    })
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _select_book(bookmakers: Sequence[dict]) -> Optional[dict]:
    """Pick the highest-priority book that posted the inning total.

    Books that didn't post the alternate market won't be in the list at
    all (the API filters per market), so any bookmaker we see here has
    a `totals_1st_1_innings` block.
    """
    if not bookmakers:
        return None
    by_key = {b.get("key", ""): b for b in bookmakers}
    for preferred in PREFERRED_BOOK_KEYS:
        if preferred in by_key:
            return by_key[preferred]
    return bookmakers[0]


def _extract_nrfi_yrfi_outcomes(
    event_payload: dict,
) -> tuple[Optional[float], Optional[float], str]:
    """Return (nrfi_odds, yrfi_odds, book_key) from a per-event payload.

    Matches on `outcome.point == 0.5` (the canonical NRFI line). Books
    occasionally post a 0 (push-only) or 1.5 line; we only count 0.5
    lines so the snapshot stays apples-to-apples across days.
    """
    book = _select_book(event_payload.get("bookmakers", []))
    if book is None:
        return None, None, ""
    nrfi_odds: Optional[float] = None
    yrfi_odds: Optional[float] = None
    for market in book.get("markets", []):
        if market.get("key") != INNING_TOTALS_MARKET:
            continue
        for outcome in market.get("outcomes", []):
            point = outcome.get("point")
            if point is None or float(point) != 0.5:
                continue
            name = (outcome.get("name") or "").strip().lower()
            price = outcome.get("price")
            if price is None:
                continue
            if name == "under":
                nrfi_odds = float(price)
            elif name == "over":
                yrfi_odds = float(price)
    return nrfi_odds, yrfi_odds, book.get("key", "")


# ---------------------------------------------------------------------------
# Game matching
# ---------------------------------------------------------------------------


def _events_for_date(events: Iterable[dict], target_date: str) -> list[dict]:
    """Filter events whose `commence_time` (UTC ISO) falls on `target_date`."""
    out: list[dict] = []
    for ev in events:
        commence = ev.get("commence_time", "")
        if not commence:
            continue
        # commence_time is an ISO-8601 string in UTC.
        if commence[:10] == target_date:
            out.append(ev)
    return out


def _match_event_to_game_pk(
    event: dict, games_df,
) -> Optional[int]:
    """Find the MLB game_pk in `games_df` that matches an Odds API event.

    Match key: (away_tricode, home_tricode). Date is implied by the
    caller filtering games_df to a single day. Returns None when the
    teams don't normalize cleanly — caller logs and skips.
    """
    home_tri = _full_name_to_tricode(event.get("home_team", ""))
    away_tri = _full_name_to_tricode(event.get("away_team", ""))
    if home_tri is None or away_tri is None:
        return None
    matches = games_df[
        (games_df["home_team"] == home_tri)
        & (games_df["away_team"] == away_tri)
    ]
    if matches.empty:
        return None
    return int(matches.iloc[0]["game_pk"])


# ---------------------------------------------------------------------------
# Public capture / lookup API
# ---------------------------------------------------------------------------


def capture_closing_lines(
    store: NRFIStore,
    game_date: str,
    *,
    config: Optional[NRFIConfig] = None,
    http_client=None,
    api_key: Optional[str] = None,
) -> int:
    """Best-effort: fetch + persist NRFI/YRFI 0.5 lines for `game_date`.

    Returns the number of (game_pk, market_type) snapshots written.
    Logs and swallows network/quota errors — settlement must never be
    blocked by an Odds API hiccup.
    """
    init_odds_tables(store)

    try:
        api_key = _resolve_api_key(api_key)
    except RuntimeError as e:
        log.warning("odds capture skipped (%s)", e)
        return 0

    games_df = store.games_for_date(game_date)
    if games_df is None or games_df.empty:
        log.info("odds capture: no games scheduled for %s", game_date)
        return 0

    # Lazy-import httpx so the module doesn't pull it for tests that
    # mock the http_client outright.
    owns_client = http_client is None
    if owns_client:
        import httpx
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        try:
            events = _fetch_event_list(
                sport_key=SPORT_KEY_MLB, api_key=api_key,
                http_client=http_client,
            )
        except Exception as e:
            log.warning("odds capture: event-list fetch failed (%s): %s",
                          type(e).__name__, e)
            return 0

        events_today = _events_for_date(events, game_date)
        if not events_today:
            log.info("odds capture: no Odds API events for %s "
                       "(API may be a day behind or no slate)", game_date)
            return 0

        snapshots: list[dict] = []
        captured_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ).replace("+00:00", "")
        for event in events_today:
            game_pk = _match_event_to_game_pk(event, games_df)
            if game_pk is None:
                log.debug("odds capture: no game_pk match for %s @ %s",
                            event.get("away_team"), event.get("home_team"))
                continue
            try:
                payload = _fetch_event_odds(
                    sport_key=SPORT_KEY_MLB, event_id=event["id"],
                    api_key=api_key, http_client=http_client,
                )
            except Exception as e:
                log.warning("odds capture: event %s fetch failed (%s): %s",
                              event.get("id"), type(e).__name__, e)
                continue
            nrfi_odds, yrfi_odds, book = _extract_nrfi_yrfi_outcomes(payload)
            if nrfi_odds is not None:
                snapshots.append({
                    "game_pk": game_pk,
                    "market_type": "NRFI",
                    "snapshot_kind": "closing",
                    "american_odds": nrfi_odds,
                    "decimal_odds": american_to_decimal(nrfi_odds),
                    "book": book,
                    "captured_at": captured_at,
                })
            if yrfi_odds is not None:
                snapshots.append({
                    "game_pk": game_pk,
                    "market_type": "YRFI",
                    "snapshot_kind": "closing",
                    "american_odds": yrfi_odds,
                    "decimal_odds": american_to_decimal(yrfi_odds),
                    "book": book,
                    "captured_at": captured_at,
                })

        if snapshots:
            store.upsert("nrfi_odds_snapshot", snapshots)
        return len(snapshots)
    finally:
        if owns_client:
            http_client.close()


def lookup_closing_odds(
    store: NRFIStore, game_pk: int, market_type: str,
) -> Optional[float]:
    """Return the captured closing american_odds for (game_pk, market_type).

    None when no snapshot was captured — caller should fall back to the
    -120 / -105 defaults so settlement still proceeds.
    """
    df = store.query_df(
        """
        SELECT american_odds
        FROM nrfi_odds_snapshot
        WHERE game_pk = ? AND market_type = ? AND snapshot_kind = 'closing'
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (int(game_pk), str(market_type)),
    )
    if df is None or df.empty:
        return None
    return float(df.iloc[0]["american_odds"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import sys
    from datetime import date

    parser = argparse.ArgumentParser(
        description="NRFI/YRFI closing-line capture (Phase 4)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cap = sub.add_parser("capture",
                              help="Fetch + persist closing lines for a date")
    p_cap.add_argument("--date", default=date.today().isoformat(),
                          help="Game date (YYYY-MM-DD). Default: today UTC.")

    p_show = sub.add_parser("show",
                               help="Print captured snapshots for a date")
    p_show.add_argument("--date", default=date.today().isoformat())

    args = parser.parse_args(list(argv) if argv is not None else None)
    cfg = get_default_config().resolve_paths()
    store = NRFIStore(cfg.duckdb_path)
    init_odds_tables(store)

    if args.cmd == "capture":
        n = capture_closing_lines(store, args.date, config=cfg)
        print(f"Captured {n} odds snapshot(s) for {args.date}")
        return 0
    if args.cmd == "show":
        df = store.query_df(
            """
            SELECT s.game_pk, g.away_team, g.home_team,
                   s.market_type, s.american_odds, s.book, s.captured_at
            FROM nrfi_odds_snapshot s JOIN games g USING(game_pk)
            WHERE g.game_date = ?
            ORDER BY g.first_pitch_ts, s.market_type
            """,
            (args.date,),
        )
        if df is None or df.empty:
            print(f"(no snapshots captured for {args.date})")
        else:
            print(df.to_string(index=False))
        return 0
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
