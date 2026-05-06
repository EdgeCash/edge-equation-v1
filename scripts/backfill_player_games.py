#!/usr/bin/env python3
"""Per-game per-player boxscore backfill from MLB Stats API.

For every game in `data/backfill/mlb/<season>/games.json`, fetch the
boxscore endpoint and extract:

  Batters:  HR / Hits / Total_Bases / RBI / atBats / plateAppearances /
            strikeOuts / baseOnBalls / sacFlies / hitByPitch
  Pitchers: K / battersFaced / inningsPitched / earnedRuns / hits /
            walks / homeRuns

Output: ``data/backfill/mlb/<season>/player_games.jsonl`` -- one JSON
record per (game_pk, player_id, role) tuple, appended as games complete.

Resumable
---------
The script remembers which game_pks already appear in the JSONL file and
skips them on resume. Crash-safe: each game's records are written + the
file is fsynced before the next request fires, so an interrupt at any
point just costs the in-flight game.

Designed to run overnight at low API rates (default 0.4 RPS = ~150
games/hour, full 2024+2025 seasons in ~32 hours wall-clock, well under
MLB Stats API's free-tier rate limits).

Usage::

    # Full backfill of 2024 + 2025 (overnight):
    python scripts/backfill_player_games.py --seasons 2024 2025

    # Resume an interrupted 2025 run:
    python scripts/backfill_player_games.py --seasons 2025

    # Quick smoke test (first 10 games of 2024):
    python scripts/backfill_player_games.py --seasons 2024 --limit 10

    # Faster pull when the operator's confident the API is healthy:
    python scripts/backfill_player_games.py --seasons 2025 --rps 1.0
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKFILL_DIR = REPO_ROOT / "data" / "backfill" / "mlb"
BASE_URL = "https://statsapi.mlb.com/api/v1"


# ---------------------------------------------------------------------
# Boxscore parsing
# ---------------------------------------------------------------------

# Fields we keep from each player's batting stats (everything else in the
# boxscore is derivable or unused). All counts; rates are recomputed from
# the per-game rows by the props backtest later.
_BATTER_FIELDS = (
    "plateAppearances", "atBats", "hits", "doubles", "triples",
    "homeRuns", "rbi", "totalBases", "baseOnBalls", "intentionalWalks",
    "hitByPitch", "strikeOuts", "sacBunts", "sacFlies",
    "groundIntoDoublePlay", "leftOnBase", "stolenBases",
)

_PITCHER_FIELDS = (
    "battersFaced", "inningsPitched", "atBats", "hits", "doubles",
    "triples", "homeRuns", "earnedRuns", "runs", "baseOnBalls",
    "intentionalWalks", "hitByPitch", "strikeOuts", "wildPitches",
    "balks", "pitchesThrown", "strikes", "balls",
)


def _coerce_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_innings(ip_str: Any) -> Optional[float]:
    """Convert 'x.y' inning strings (where y is in thirds) to a float.
    '6.2' -> 6 + 2/3 = 6.6667. Used so per-game IP totals are summable.
    """
    if ip_str is None or ip_str == "":
        return None
    try:
        s = str(ip_str)
        if "." in s:
            whole, frac = s.split(".", 1)
            return float(whole) + (int(frac) / 3.0 if frac else 0.0)
        return float(s)
    except (TypeError, ValueError):
        return None


def _extract_player_records(
    game_pk: int,
    date: str,
    home_team: str,
    away_team: str,
    boxscore: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Pull one row per (player, role) from a boxscore JSON payload."""
    rows: List[Dict[str, Any]] = []
    for side in ("home", "away"):
        team_block = (boxscore.get("teams") or {}).get(side) or {}
        team_meta = team_block.get("team") or {}
        team_id = team_meta.get("id")
        team_abbr = home_team if side == "home" else away_team
        is_home = side == "home"
        opp_abbr = away_team if is_home else home_team

        players = team_block.get("players") or {}
        starters_batting: Set[int] = set(
            (team_block.get("battingOrder") or [])[:9]
        )
        # First entry of `pitchers` is the starting pitcher.
        pitchers_seq = team_block.get("pitchers") or []
        starter_pid = pitchers_seq[0] if pitchers_seq else None

        for raw_pid, pdata in players.items():
            try:
                player_id = int(str(raw_pid).lstrip("ID"))
            except (TypeError, ValueError):
                continue
            person = pdata.get("person") or {}
            position = (pdata.get("position") or {}).get("abbreviation") or ""
            stats = pdata.get("stats") or {}
            batting = stats.get("batting") or {}
            pitching = stats.get("pitching") or {}

            # Emit a batter row when the player saw any plate appearances.
            pa = _coerce_int(batting.get("plateAppearances"))
            if pa and pa > 0:
                rows.append({
                    "game_pk": int(game_pk),
                    "date": date,
                    "team": team_abbr,
                    "team_id": team_id,
                    "opponent": opp_abbr,
                    "is_home": is_home,
                    "role": "batter",
                    "player_id": player_id,
                    "player_name": person.get("fullName"),
                    "position": position,
                    "started": player_id in starters_batting,
                    "stats": {
                        f: _coerce_int(batting.get(f))
                        for f in _BATTER_FIELDS
                    },
                })

            # Emit a pitcher row when the player faced any batters.
            bf = _coerce_int(pitching.get("battersFaced"))
            if bf and bf > 0:
                pitching_stats = {
                    f: _coerce_int(pitching.get(f))
                    for f in _PITCHER_FIELDS
                    if f != "inningsPitched"
                }
                pitching_stats["inningsPitched"] = _parse_innings(
                    pitching.get("inningsPitched"),
                )
                rows.append({
                    "game_pk": int(game_pk),
                    "date": date,
                    "team": team_abbr,
                    "team_id": team_id,
                    "opponent": opp_abbr,
                    "is_home": is_home,
                    "role": "pitcher",
                    "player_id": player_id,
                    "player_name": person.get("fullName"),
                    "position": position,
                    "started": (starter_pid == player_id),
                    "stats": pitching_stats,
                })

    return rows


# ---------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------

def _scan_completed(jsonl_path: Path) -> Set[int]:
    """Return the set of game_pks already present in the JSONL file."""
    completed: Set[int] = set()
    if not jsonl_path.exists():
        return completed
    try:
        with jsonl_path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    pk = rec.get("game_pk")
                    if pk is not None:
                        completed.add(int(pk))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return completed
    return completed


# ---------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------

@dataclass
class FetchStats:
    fetched: int = 0
    skipped: int = 0
    errors: int = 0
    rows_written: int = 0
    started_at: float = 0.0


def _format_eta(remaining: int, rps: float) -> str:
    if rps <= 0:
        return "?"
    seconds = remaining / rps
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fetch_boxscore(
    session: requests.Session, game_pk: int, retries: int = 3,
    backoff: float = 2.0,
) -> Optional[Dict[str, Any]]:
    """GET /game/{pk}/boxscore with retries. Returns None on permanent failure."""
    url = f"{BASE_URL}/game/{game_pk}/boxscore"
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 502, 503, 504):
                time.sleep(backoff * (attempt + 1))
                continue
            return None  # 4xx other than 429 -> game just doesn't have a box
        except (requests.RequestException, json.JSONDecodeError):
            time.sleep(backoff * (attempt + 1))
            continue
    return None


def backfill_season(
    season: int,
    backfill_dir: Path = DEFAULT_BACKFILL_DIR,
    rps: float = 0.4,
    limit: Optional[int] = None,
    log_every: int = 25,
) -> FetchStats:
    """Pull boxscores for every game in <season>/games.json and append
    player records to <season>/player_games.jsonl. Resumable."""
    season_dir = backfill_dir / str(season)
    games_path = season_dir / "games.json"
    if not games_path.exists():
        print(f"[{season}] games.json missing -- nothing to backfill")
        return FetchStats(started_at=time.time())

    out_path = season_dir / "player_games.jsonl"
    games = json.loads(games_path.read_text())
    completed = _scan_completed(out_path)
    pending = [g for g in games if int(g.get("game_pk", 0)) not in completed]
    if limit is not None:
        pending = pending[:limit]

    print(
        f"[{season}] {len(games)} games on disk, {len(completed)} already "
        f"backfilled, {len(pending)} to fetch"
    )
    stats = FetchStats(started_at=time.time(), skipped=len(completed))
    if not pending:
        print(f"[{season}] nothing to do.")
        return stats

    interval = 1.0 / max(rps, 0.01)
    interrupted = False

    def _on_sigint(signum, frame):  # pragma: no cover -- signal handler
        nonlocal interrupted
        interrupted = True
        print("\n[interrupt received -- finishing current request, then exiting]")

    prev_handler = signal.signal(signal.SIGINT, _on_sigint)

    session = requests.Session()
    session.headers.update({"User-Agent": "edge-equation-backfill/1.0"})

    try:
        with out_path.open("a") as out:
            last_request_at = 0.0
            for i, game in enumerate(pending, start=1):
                if interrupted:
                    break
                # Token-bucket-ish rate limiter.
                wait = (last_request_at + interval) - time.time()
                if wait > 0:
                    time.sleep(wait)
                last_request_at = time.time()

                pk = int(game["game_pk"])
                box = _fetch_boxscore(session, pk)
                if box is None:
                    stats.errors += 1
                    # Write a sentinel so resume skips this game next time.
                    sentinel = {
                        "game_pk": pk,
                        "date": game.get("date"),
                        "_error": "boxscore_unavailable",
                    }
                    out.write(json.dumps(sentinel) + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                    continue

                rows = _extract_player_records(
                    pk,
                    game.get("date", ""),
                    game.get("home_team", ""),
                    game.get("away_team", ""),
                    box,
                )
                for row in rows:
                    out.write(json.dumps(row) + "\n")
                out.flush()
                os.fsync(out.fileno())
                stats.fetched += 1
                stats.rows_written += len(rows)

                if i % log_every == 0:
                    elapsed = time.time() - stats.started_at
                    real_rps = stats.fetched / elapsed if elapsed > 0 else 0.0
                    eta = _format_eta(
                        len(pending) - i, max(real_rps, 0.01),
                    )
                    print(
                        f"  [{season}] {i:>5}/{len(pending)}  "
                        f"errors={stats.errors:<3}  "
                        f"rows={stats.rows_written:<6}  "
                        f"rps={real_rps:.2f}  ETA={eta}"
                    )
    finally:
        signal.signal(signal.SIGINT, prev_handler)

    elapsed = time.time() - stats.started_at
    print(
        f"[{season}] done -- fetched {stats.fetched}, errors {stats.errors}, "
        f"rows written {stats.rows_written}, elapsed {elapsed:.0f}s"
    )
    return stats


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backfill_player_games",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--seasons", type=int, nargs="+", required=True,
        help="One or more season years to backfill (e.g. 2024 2025).",
    )
    parser.add_argument(
        "--backfill-dir", type=Path, default=DEFAULT_BACKFILL_DIR,
        help="Override the data dir (default: data/backfill/mlb).",
    )
    parser.add_argument(
        "--rps", type=float, default=0.4,
        help="Max requests/sec to MLB Stats API (default 0.4 = polite).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap total games per season (handy for smoke tests).",
    )
    args = parser.parse_args(argv)

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[backfill_player_games] start {started}")
    print(f"  seasons={args.seasons}  rps={args.rps}  limit={args.limit}")

    total = FetchStats(started_at=time.time())
    for season in args.seasons:
        s = backfill_season(
            season=season, backfill_dir=args.backfill_dir,
            rps=args.rps, limit=args.limit,
        )
        total.fetched += s.fetched
        total.skipped += s.skipped
        total.errors += s.errors
        total.rows_written += s.rows_written

    elapsed = time.time() - total.started_at
    print(
        f"[backfill_player_games] done in {elapsed:.0f}s -- "
        f"fetched {total.fetched}, skipped {total.skipped}, "
        f"errors {total.errors}, rows {total.rows_written}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
