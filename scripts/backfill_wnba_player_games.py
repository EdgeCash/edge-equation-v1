#!/usr/bin/env python3
"""Per-game per-player WNBA boxscore backfill from ESPN.

Walks ``data/backfill/wnba/<season>/games.json`` (already on disk for
2021-2026) and fetches the ESPN summary endpoint for each game_id,
extracting every player's PTS / REB / AST / 3PM / minutes / FG /
turnovers / steals / blocks.

Output: ``data/backfill/wnba/<season>/player_games.jsonl`` -- one row
per (game, player), append-only and resumable. Same pattern as the MLB
props backfill so the future WNBA props backtest can mirror
``exporters/mlb/props_backtest.py`` line-for-line.

ESPN endpoint
~~~~~~~~~~~~~
``https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary?event=<gameId>``

Free, no auth. Default 1.0 RPS gives ~3600 games/hour wall-clock, so a
full 2021-2025 backfill (~1300 games) finishes in under 30 minutes.

Usage::

    # Smoke first (ESPN's gameId space + the WNBA games.json on disk):
    python scripts/backfill_wnba_player_games.py --seasons 2024 --limit 5

    # Full overnight pull:
    python scripts/backfill_wnba_player_games.py --seasons 2021 2022 2023 2024 2025
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harvest_common import (  # noqa: E402
    FetchStats, RateLimitedClient, graceful_jsonl_writer, graceful_sigint,
    log_progress, scan_completed_ids, utc_iso,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKFILL_DIR = REPO_ROOT / "data" / "backfill" / "wnba"

ESPN_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/"
    "summary?event={event_id}"
)


# Stats columns we want from ESPN's boxscore. ESPN exposes them as
# parallel ``labels`` + ``stats`` arrays per athlete; we map labels -> ints.
# The label set ESPN ships for WNBA boxscores (verified against multiple
# 2024 games): MIN, FG, 3PT, FT, OREB, DREB, REB, AST, STL, BLK, TO, PF,
# PLUS_MINUS, PTS. We extract a curated subset.
_INT_FIELDS = {
    "PTS":  "points",
    "REB":  "rebounds",
    "AST":  "assists",
    "OREB": "oreb",
    "DREB": "dreb",
    "STL":  "steals",
    "BLK":  "blocks",
    "TO":   "turnovers",
    "PF":   "fouls",
    "MIN":  "minutes",
}


def _coerce_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    s = str(v).strip()
    if s in ("--", "-"):
        return 0
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _parse_made_attempt(v: Any) -> Dict[str, Optional[int]]:
    """ESPN encodes shooting like '8-15'. Returns {made, attempts}.
    Handles missing / malformed entries by returning Nones."""
    if v is None:
        return {"made": None, "attempts": None}
    s = str(v)
    if "-" not in s:
        return {"made": None, "attempts": None}
    a, _, b = s.partition("-")
    try:
        return {"made": int(a), "attempts": int(b)}
    except (TypeError, ValueError):
        return {"made": None, "attempts": None}


def parse_summary(
    payload: Dict[str, Any], game_meta: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """ESPN's summary payload exposes ``boxscore.players[]`` -- one entry
    per team, each with ``statistics[].athletes[].stats``. We flatten
    one row per (athlete, team)."""
    rows: List[Dict[str, Any]] = []
    boxscore = payload.get("boxscore") or {}
    teams_block = boxscore.get("players") or []
    if not teams_block:
        return rows

    for team in teams_block:
        team_meta = team.get("team") or {}
        team_abbr = team_meta.get("abbreviation") or ""
        is_home = team_meta.get("id") == (game_meta.get("home_id") or "")
        opp_abbr = (
            game_meta.get("away_team") if is_home
            else game_meta.get("home_team")
        )
        for stat_block in team.get("statistics") or []:
            labels = stat_block.get("labels") or stat_block.get("names") or []
            for ath in stat_block.get("athletes") or []:
                stats = ath.get("stats") or []
                if not stats:
                    continue
                # zip labels -> stats so we can pluck by name
                by_label = dict(zip(labels, stats))
                row: Dict[str, Any] = {
                    "game_id":      str(game_meta.get("game_id")),
                    "date":         game_meta.get("date"),
                    "team":         team_abbr,
                    "opponent":     opp_abbr,
                    "is_home":      is_home,
                    "player_id":    int((ath.get("athlete") or {}).get("id") or 0),
                    "player_name":  (ath.get("athlete") or {}).get("displayName"),
                    "position":     (
                        (ath.get("athlete") or {}).get("position") or {}
                    ).get("abbreviation"),
                    "starter":      bool(ath.get("starter") or False),
                    "did_not_play": bool(ath.get("didNotPlay") or False),
                }
                # Direct integer stats.
                for label, key in _INT_FIELDS.items():
                    if label == "MIN":
                        # MIN is sometimes "MM:SS" -- try both.
                        val = by_label.get(label)
                        if val and ":" in str(val):
                            try:
                                m, s = str(val).split(":")
                                row[key] = int(m) + int(s) / 60.0
                                continue
                            except (TypeError, ValueError):
                                pass
                        row[key] = _coerce_int(val)
                    else:
                        row[key] = _coerce_int(by_label.get(label))
                # Shooting splits: FG / 3PT / FT.
                fg = _parse_made_attempt(by_label.get("FG"))
                three = _parse_made_attempt(by_label.get("3PT"))
                ft = _parse_made_attempt(by_label.get("FT"))
                row["fg_made"]  = fg["made"]
                row["fg_att"]   = fg["attempts"]
                row["three_made"] = three["made"]
                row["three_att"]  = three["attempts"]
                row["ft_made"]  = ft["made"]
                row["ft_att"]   = ft["attempts"]
                # Composite props markets the future engine will care about.
                if row.get("points") is not None and row.get("rebounds") is not None \
                        and row.get("assists") is not None:
                    row["pra"] = row["points"] + row["rebounds"] + row["assists"]
                rows.append(row)
    return rows


def _load_games_json(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def backfill_season(
    season: int,
    backfill_dir: Path = DEFAULT_BACKFILL_DIR,
    rps: float = 1.0,
    limit: Optional[int] = None,
    log_every: int = 25,
) -> FetchStats:
    season_dir = backfill_dir / str(season)
    games_path = season_dir / "games.json"
    out_path = season_dir / "player_games.jsonl"
    games = _load_games_json(games_path)
    if not games:
        print(f"[WNBA-{season}] games.json missing -- skip")
        return FetchStats()
    completed = scan_completed_ids(out_path, id_field="game_id")
    pending = [g for g in games if str(g.get("game_id")) not in completed]
    if limit is not None:
        pending = pending[:limit]

    print(
        f"[WNBA-{season}] {len(games)} games on disk, "
        f"{len(completed)} already done, {len(pending)} pending"
    )
    stats = FetchStats(skipped=len(completed))
    if not pending:
        print(f"[WNBA-{season}] nothing to do.")
        return stats

    client = RateLimitedClient(rps=rps)
    with graceful_sigint() as was_interrupted, \
         graceful_jsonl_writer(out_path) as write:
        for i, game in enumerate(pending, start=1):
            if was_interrupted():
                break
            game_id = str(game.get("game_id"))
            url = ESPN_URL.format(event_id=game_id)
            payload = client.get_json(url)
            if payload is None:
                stats.errors += 1
                write({"game_id": game_id, "_error": "summary_unavailable"})
                continue
            rows = parse_summary(payload, {
                "game_id":   game_id,
                "date":      game.get("date"),
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "home_id":   game.get("home_id"),
            })
            if not rows:
                write({"game_id": game_id, "_no_player_rows": True})
                stats.fetched += 1
                continue
            for row in rows:
                write(row)
                stats.rows_written += 1
            stats.fetched += 1
            log_progress(f"WNBA-{season}", i, len(pending), stats, log_every)

    elapsed = time.time() - stats.started_at
    print(
        f"[WNBA-{season}] done in {elapsed:.0f}s -- "
        f"games fetched {stats.fetched}, errors {stats.errors}, "
        f"rows {stats.rows_written}"
    )
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="backfill_wnba_player_games")
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument("--backfill-dir", type=Path, default=DEFAULT_BACKFILL_DIR)
    parser.add_argument("--rps", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    print(f"[backfill_wnba_player_games] start {utc_iso()}")
    print(f"  seasons={args.seasons} rps={args.rps} limit={args.limit}")
    total = FetchStats()
    for s in args.seasons:
        st = backfill_season(s, args.backfill_dir, args.rps, args.limit)
        total.fetched += st.fetched
        total.errors += st.errors
        total.rows_written += st.rows_written
    print(
        f"[backfill_wnba_player_games] done -- "
        f"games {total.fetched}, errors {total.errors}, rows {total.rows_written}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
