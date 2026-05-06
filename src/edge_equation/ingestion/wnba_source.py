"""WNBA ingestion stub.

Minimal source the engine module's run_daily.py + backtest_historical.py
import. Until the live PrizePicks / WNBA-stats pull lands, these helpers
fall through to:

  1. The on-disk slate / projections written by
     `edge_equation.exporters.wnba.daily` (the orchestrator already
     producing wnba_daily.json) when present.
  2. A conservative empty list otherwise.

Keeping this stub here means a fresh checkout can `python -m
edge_equation.engines.wnba.run_daily` without hitting any external
service AND without ImportError, which unblocks Friday opening night.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# Public: where the exporter writes its JSON. The engine reads from here
# when no other ingestion is wired up.
_DEFAULT_DAILY = (
    Path(__file__).resolve().parents[3]
    / "website" / "public" / "data" / "wnba" / "wnba_daily.json"
)
_DEFAULT_BACKFILL_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "backfill" / "wnba"
)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_daily(path: Path = _DEFAULT_DAILY) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_daily_games(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the day's WNBA slate as a list of ``{away_team, home_team,
    game_time, ...}`` dicts. Falls through to whatever the exporter wrote
    for ``date`` (or today). Returns ``[]`` when nothing is on disk."""
    payload = _load_daily()
    if not payload:
        return []
    target = date or _today_utc()
    if payload.get("date") != target:
        return []
    return list(payload.get("slate") or [])


def get_player_props(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return today's player-prop offers. The current MVP exporter
    emits game markets only (ML / spread / totals), so this returns
    those rows shaped as props -- the runner skips unknown markets."""
    payload = _load_daily()
    if not payload:
        return []
    target = date or _today_utc()
    if payload.get("date") != target:
        return []
    out: List[Dict[str, Any]] = []
    for pick in payload.get("all_picks", []):
        market_type = pick.get("bet_type")
        if market_type == "moneyline":
            mt = "fullgame_ml"
        elif market_type == "spread":
            mt = "fullgame_spread"
        elif market_type == "totals":
            mt = "fullgame_total"
        else:
            mt = market_type
        out.append({
            "market_type": mt,
            "player": pick.get("matchup"),
            "team": (pick.get("matchup") or "@").split("@")[-1],
            "opponent": (pick.get("matchup") or "@").split("@")[0],
            "line": _line_from_pick(pick),
            "model_prob": pick.get("model_prob"),
            "edge_pct": pick.get("edge_pct"),
            "decimal_odds": pick.get("decimal_odds"),
            "book": pick.get("book"),
            "usage": 0.20, "possessions": 70.0, "shot_quality": 1.0,
            "rebound_chance": 0.10, "minutes": 30.0, "assist_rate": 0.05,
            "three_rate": 0.10, "three_accuracy": 0.35,
            "team_ppp": 1.0, "opp_ppp": 1.0,
        })
    return out


def _line_from_pick(pick: Dict[str, Any]) -> float:
    """Extract a numeric line from a pick row's textual `pick` field
    (e.g. "OVER 162.5" or "+5.5"). Returns 0.0 when no number is found."""
    raw = pick.get("pick")
    if raw is None:
        return 0.0
    for tok in str(raw).replace("+", " ").replace("-", " -").split():
        try:
            return float(tok)
        except ValueError:
            continue
    return 0.0


def get_historical_props(start: str, end: str) -> List[Dict[str, Any]]:
    """Walk the backfill dir for game rows in ``[start, end]`` and shape
    them as historical prop records the WNBABacktester can replay.

    Data shape: ``data/backfill/wnba/<season>/games.json`` is a list of
    ``{date, away_team, home_team, away_score, home_score}`` rows. We
    emit one synthetic historical "prop" per game per market (ML, total,
    spread) so the simple aggregation in WNBABacktester runs end to end.
    """
    rows: List[Dict[str, Any]] = []
    if not _DEFAULT_BACKFILL_DIR.exists():
        return rows
    for season_dir in sorted(_DEFAULT_BACKFILL_DIR.iterdir()):
        path = season_dir / "games.json"
        if not path.is_file():
            continue
        try:
            games = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for g in games:
            d = g.get("date", "")
            if not (start <= d <= end):
                continue
            rows.extend(_explode_game(g))
    return rows


def _explode_game(g: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    away = g.get("away_team")
    home = g.get("home_team")
    aw = g.get("away_score") or 0
    hm = g.get("home_score") or 0
    total = aw + hm
    base = {
        "team": home, "opponent": away,
        "usage": 0.20, "possessions": 70.0, "shot_quality": 1.0,
        "rebound_chance": 0.10, "minutes": 30.0, "assist_rate": 0.05,
        "three_rate": 0.10, "three_accuracy": 0.35,
        "team_ppp": 1.0, "opp_ppp": 1.0,
    }
    yield {**base, "market": "fullgame_total", "player": f"{away}@{home}",
           "line": 162.5, "actual": total}
    yield {**base, "market": "fullgame_ml", "player": f"{away}@{home}",
           "line": 0.5, "actual": 1.0 if hm > aw else 0.0}
    yield {**base, "market": "fullgame_spread", "player": f"{away}@{home}",
           "line": 5.5, "actual": float(hm - aw)}
