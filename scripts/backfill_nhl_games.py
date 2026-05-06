#!/usr/bin/env python3
"""NHL games backfill from ESPN's free scoreboard API.

Almost identical to ``backfill_nba_games.py`` -- ESPN's URL pivot is
the only thing that changes between sports, and using the same source
keeps the schema consistent across all four pro leagues we backfill
(MLB games come from MLB Stats API directly because boxscores there
have richer per-player detail).

NHL season convention: spans calendar years; "season N" == year N
regular season ending (e.g. 2023-24 = season 2024). Window: Sep 15
(year N-1) -> Jun 30 (year N).

Output: ``data/backfill/nhl/<season>/games.jsonl``.

Usage::

    python scripts/backfill_nhl_games.py --seasons 2024 2025
    python scripts/backfill_nhl_games.py --seasons 2024 --limit 7
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harvest_common import (  # noqa: E402
    FetchStats, RateLimitedClient, graceful_jsonl_writer, graceful_sigint,
    log_progress, scan_completed_ids, utc_iso,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKFILL_DIR = REPO_ROOT / "data" / "backfill" / "nhl"

ESPN_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/"
    "scoreboard?dates={ymd}"
)


def season_window(season: int) -> Tuple[date, date]:
    return (date(season - 1, 9, 15), date(season, 6, 30))


def iter_dates(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _linescores(competitor: Dict[str, Any]) -> List[int]:
    out: List[int] = []
    for ls in competitor.get("linescores") or []:
        try:
            out.append(int(ls.get("value") or ls.get("displayValue") or 0))
        except (TypeError, ValueError):
            out.append(0)
    return out


def parse_scoreboard(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per completed game. NHL scoreboard payloads are
    structurally identical to NBA's -- competitions[].competitors[] +
    score / homeAway / linescores."""
    rows: List[Dict[str, Any]] = []
    for event in payload.get("events") or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        status = ((comp.get("status") or {}).get("type") or {}).get("name") or ""
        completed = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
        if not completed:
            continue
        away = home = None
        for c in comp.get("competitors") or []:
            side = c.get("homeAway")
            if side == "home":
                home = c
            elif side == "away":
                away = c
        if not home or not away:
            continue
        try:
            home_score = int(home.get("score") or 0)
            away_score = int(away.get("score") or 0)
        except (TypeError, ValueError):
            continue
        home_abbr = (home.get("team") or {}).get("abbreviation") or ""
        away_abbr = (away.get("team") or {}).get("abbreviation") or ""
        if not (home_abbr and away_abbr):
            continue
        ml_winner = home_abbr if home_score > away_score else away_abbr
        margin = abs(home_score - away_score)
        away_p = _linescores(away)
        home_p = _linescores(home)
        # Periods 1-3 are regulation; anything beyond is OT/SO.
        regulation_away = sum(away_p[:3])
        regulation_home = sum(home_p[:3])
        rows.append({
            "date": (comp.get("date") or "")[:10],
            "game_id": str(event.get("id") or comp.get("id")),
            "season_type": int(((event.get("season") or {}).get("type") or 0) or 0),
            "status": status,
            "completed": completed,
            "away_team": away_abbr,
            "home_team": home_abbr,
            "away_score": away_score,
            "home_score": home_score,
            "total_goals": home_score + away_score,
            "ml_winner": ml_winner,
            "margin": margin,
            "puck_line_margin": margin,  # alias for the spread market
            "away_periods": away_p,
            "home_periods": home_p,
            "regulation_total": regulation_away + regulation_home,
            "regulation_winner": (
                home_abbr if regulation_home > regulation_away
                else away_abbr if regulation_away > regulation_home
                else "TIE"
            ),
            "had_ot": len(home_p) > 3 or len(away_p) > 3,
            "venue": (comp.get("venue") or {}).get("fullName"),
        })
    return rows


def backfill_season(
    season: int,
    backfill_dir: Path = DEFAULT_BACKFILL_DIR,
    rps: float = 1.0,
    limit: Optional[int] = None,
    log_every: int = 25,
) -> FetchStats:
    season_dir = backfill_dir / str(season)
    out_path = season_dir / "games.jsonl"
    completed = scan_completed_ids(out_path, id_field="_date_ymd")
    start, end = season_window(season)
    days = list(iter_dates(start, end))
    pending = [d for d in days if d.strftime("%Y%m%d") not in completed]
    if limit is not None:
        pending = pending[:limit]

    print(
        f"[NHL-{season}] window {start} -> {end} "
        f"({len(days)} days, {len(completed)} cached, {len(pending)} pending)"
    )
    stats = FetchStats(skipped=len(completed))
    if not pending:
        print(f"[NHL-{season}] nothing to do.")
        return stats

    client = RateLimitedClient(rps=rps)
    with graceful_sigint() as was_interrupted, \
         graceful_jsonl_writer(out_path) as write:
        for i, d in enumerate(pending, start=1):
            if was_interrupted():
                break
            ymd = d.strftime("%Y%m%d")
            url = ESPN_URL.format(ymd=ymd)
            payload = client.get_json(url)
            if payload is None:
                stats.errors += 1
                write({"_date_ymd": ymd, "_error": "scoreboard_unavailable"})
                continue
            rows = parse_scoreboard(payload)
            for row in rows:
                row["_date_ymd"] = ymd
                write(row)
                stats.rows_written += 1
            if not rows:
                write({"_date_ymd": ymd, "_no_games": True})
            stats.fetched += 1
            log_progress(f"NHL-{season}", i, len(pending), stats, log_every)

    elapsed = time.time() - stats.started_at
    print(
        f"[NHL-{season}] done in {elapsed:.0f}s -- "
        f"days fetched {stats.fetched}, errors {stats.errors}, "
        f"rows {stats.rows_written}"
    )
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="backfill_nhl_games")
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument("--backfill-dir", type=Path, default=DEFAULT_BACKFILL_DIR)
    parser.add_argument("--rps", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    print(f"[backfill_nhl_games] start {utc_iso()}")
    print(f"  seasons={args.seasons} rps={args.rps} limit={args.limit}")
    total = FetchStats()
    for s in args.seasons:
        st = backfill_season(s, args.backfill_dir, args.rps, args.limit)
        total.fetched += st.fetched
        total.errors += st.errors
        total.rows_written += st.rows_written
    print(
        f"[backfill_nhl_games] done -- days {total.fetched}, "
        f"errors {total.errors}, games {total.rows_written}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
