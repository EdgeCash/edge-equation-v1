"""
PrizePicks projections fetcher → CSV.

Server-side replacement for the iOS Shortcut path. Hits the PrizePicks
public projections API, traverses the JSON-API `data` + `included`
relationship graph (which is painful in iOS Shortcuts but trivial here),
and writes a 12-column CSV that pastes 1:1 into the `_raw` sheet of
edge_equation_props.xlsx (see tools/sheets/build_numbers_workbook.py).

Run:
    python3 tools/sheets/fetch_prizepicks.py
    python3 tools/sheets/fetch_prizepicks.py --out path/to/out.csv
    python3 tools/sheets/fetch_prizepicks.py --max-pages 1   # quick test

GitHub Actions calls this hourly via .github/workflows/prizepicks-fetch.yml,
commits the output to data/prizepicks/latest.csv, and lets the user
download from a stable raw GitHub URL on iPhone Safari.

Output columns (matches Numbers `_raw` layout):
    scraped_at, projection_id, league, sport, player, team, position,
    description, stat_type, line, start_time, odds_type
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PP_API = "https://api.prizepicks.com/projections"
PER_PAGE = 250
MAX_PAGES_DEFAULT = 30
PAGE_DELAY_SEC = 0.4

# A current-iPhone-Safari UA. Used both as the request User-Agent and
# as a hint to curl_cffi's TLS-fingerprint impersonation (we use the
# matching `safari17_0` profile below).
DEFAULT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

# Apr 26, 2026: PrizePicks moved their public API behind PerimeterX
# (HUMAN Security) bot detection. Plain urllib + custom User-Agent
# returns HTTP 403 with a PerimeterX challenge response (appId,
# firstPartyEnabled, blockScript fields). curl_cffi wraps libcurl-
# impersonate to match real-browser TLS fingerprints (cipher suite
# order, ALPN, extensions), which gets us past the network-layer
# detection. We also rotate through multiple impersonation profiles
# on consecutive 403s in case one specific fingerprint gets stale.
#
# Reference: https://github.com/lexiforest/curl_cffi
IMPERSONATE_PROFILES = ("safari17_0", "chrome120", "safari15_5")

CSV_COLUMNS = [
    "scraped_at", "projection_id", "league", "sport", "player",
    "team", "position", "description", "stat_type", "line",
    "start_time", "odds_type",
]


def _fetch_page(
    page: int,
    *,
    user_agent: str = DEFAULT_UA,
    impersonate_profiles: Tuple[str, ...] = IMPERSONATE_PROFILES,
) -> Dict[str, Any]:
    """Single API call. Returns the parsed JSON dict.

    Tries each impersonation profile in order until one returns 200 or
    we exhaust the list. PerimeterX may accept some Safari fingerprints
    while rejecting others; rotating gives the workflow more shots.

    Raises RuntimeError on persistent failures with enough detail that
    the workflow log shows the cause clearly (status code + first 200
    bytes of the response body).
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        raise RuntimeError(
            "curl_cffi is required to bypass PrizePicks' PerimeterX "
            "challenge. Install it with: pip install curl_cffi"
        ) from None

    url = f"{PP_API}?per_page={PER_PAGE}&page={page}"
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        # PrizePicks' web client sends these; matching reduces the
        # chance their server-side filter flags us as a non-browser.
        "Origin": "https://app.prizepicks.com",
        "Referer": "https://app.prizepicks.com/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    last_status: Optional[int] = None
    last_snippet: str = ""
    for profile in impersonate_profiles:
        try:
            resp = cffi_requests.get(
                url,
                headers=headers,
                impersonate=profile,
                timeout=20,
            )
        except Exception as e:
            last_status = -1
            last_snippet = f"network error with profile={profile}: {e}"
            continue
        if resp.status_code == 200:
            return resp.json()
        last_status = resp.status_code
        last_snippet = (resp.text or "")[:200]
        # 429 or 5xx: short backoff before next profile try.
        if resp.status_code in (429, 502, 503, 504):
            time.sleep(2.0)

    raise RuntimeError(
        f"PrizePicks API HTTP {last_status} on page {page} after trying "
        f"profiles {list(impersonate_profiles)}: {last_snippet}"
    )


def _build_lookup_tables(
    payload: Dict[str, Any],
    players: Dict[str, Dict[str, Any]],
    leagues: Dict[str, Dict[str, Any]],
) -> None:
    """Extend `players` and `leagues` with this page's `included`
    entries. PrizePicks uses JSON:API: each projection links to a
    `new_player` and a `league` by id; the actual records arrive in
    a separate `included` array we accumulate across pages."""
    for inc in payload.get("included") or []:
        kind = inc.get("type")
        ent_id = inc.get("id")
        attrs = inc.get("attributes") or {}
        if not ent_id:
            continue
        if kind == "new_player":
            players[ent_id] = attrs
        elif kind == "league":
            leagues[ent_id] = attrs


def _projection_to_row(
    proj: Dict[str, Any],
    players: Dict[str, Dict[str, Any]],
    leagues: Dict[str, Dict[str, Any]],
    scraped_at: str,
) -> Optional[List[str]]:
    """Map one projection record + its related player/league entries
    to the 12-column row used by the Numbers `_raw` sheet. Returns
    None for malformed records so the caller can skip + count them."""
    if not isinstance(proj, dict):
        return None
    proj_id = proj.get("id")
    if not proj_id:
        return None
    a = proj.get("attributes") or {}
    r = proj.get("relationships") or {}

    player_id = ((r.get("new_player") or {}).get("data") or {}).get("id")
    league_id = ((r.get("league") or {}).get("data") or {}).get("id")
    pl = players.get(player_id, {}) if player_id else {}
    lg = leagues.get(league_id, {}) if league_id else {}

    line_score = a.get("line_score")
    line_str = "" if line_score is None else str(line_score)

    return [
        scraped_at,
        str(proj_id),
        str(lg.get("name") or ""),
        str(lg.get("sport") or ""),
        str(pl.get("name") or pl.get("display_name") or ""),
        str(pl.get("team") or pl.get("team_name") or ""),
        str(pl.get("position") or ""),
        str(a.get("description") or ""),
        str(a.get("stat_type") or ""),
        line_str,
        str(a.get("start_time") or ""),
        str(a.get("odds_type") or "standard"),
    ]


def fetch_all(
    *,
    max_pages: int = MAX_PAGES_DEFAULT,
    user_agent: str = DEFAULT_UA,
    league_filter: Optional[str] = None,
) -> Tuple[List[List[str]], Dict[str, int]]:
    """Page through the projections endpoint until either `max_pages`
    is hit or a page returns no projections.

    Returns (rows, summary) where summary is a small dict of audit
    counters the workflow can echo to its log (pages_fetched,
    projections_seen, projections_skipped, leagues_seen, players_seen).
    """
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    players: Dict[str, Dict[str, Any]] = {}
    leagues: Dict[str, Dict[str, Any]] = {}
    rows: List[List[str]] = []
    seen = 0
    skipped = 0
    pages_fetched = 0

    for page in range(1, max_pages + 1):
        payload = _fetch_page(page, user_agent=user_agent)
        pages_fetched += 1
        _build_lookup_tables(payload, players, leagues)
        data = payload.get("data") or []
        if not data:
            break
        for proj in data:
            seen += 1
            row = _projection_to_row(proj, players, leagues, scraped_at)
            if row is None:
                skipped += 1
                continue
            if league_filter and row[2].lower() != league_filter.lower():
                # column 2 is `league`; cheap post-filter so callers can
                # restrict to MLB without server-side query support.
                continue
            rows.append(row)
        # Stop early if we got fewer than PER_PAGE -- last page reached.
        if len(data) < PER_PAGE:
            break
        time.sleep(PAGE_DELAY_SEC)

    summary = {
        "pages_fetched": pages_fetched,
        "projections_seen": seen,
        "projections_skipped": skipped,
        "rows_written": len(rows),
        "leagues_seen": len(leagues),
        "players_seen": len(players),
    }
    return rows, summary


def write_csv(rows: List[List[str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        w.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--out", type=str, default="data/prizepicks/latest.csv",
        help="Output CSV path. Default: data/prizepicks/latest.csv.",
    )
    parser.add_argument(
        "--max-pages", type=int, default=MAX_PAGES_DEFAULT,
        help=f"Hard cap on pages to fetch. Default {MAX_PAGES_DEFAULT}.",
    )
    parser.add_argument(
        "--league", type=str, default=None,
        help="Filter rows to one league name (e.g., MLB). "
             "Default: include all leagues PrizePicks returns.",
    )
    parser.add_argument(
        "--user-agent", type=str, default=DEFAULT_UA,
        help="HTTP User-Agent. Default is a current iPhone Safari UA.",
    )
    parser.add_argument(
        "--snapshot-dir", type=str, default=None,
        help="If set, also writes a timestamped snapshot CSV here so "
             "history accumulates for player-accuracy analysis. The "
             "workflow uses data/prizepicks/snapshots/.",
    )
    args = parser.parse_args(argv)

    try:
        rows, summary = fetch_all(
            max_pages=args.max_pages,
            user_agent=args.user_agent,
            league_filter=args.league,
        )
    except RuntimeError as e:
        print(f"FETCH FAILED: {e}", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    write_csv(rows, out_path)
    print(f"wrote {out_path}  ({len(rows)} rows)")

    if args.snapshot_dir:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        snap = Path(args.snapshot_dir) / f"prizepicks_{ts}.csv"
        write_csv(rows, snap)
        print(f"wrote snapshot {snap}")

    print(f"summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
