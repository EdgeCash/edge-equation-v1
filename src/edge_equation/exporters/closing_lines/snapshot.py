"""Closing-line snapshot logger.

Fetches the current Odds API snapshot for a sport and appends one flat
row per (event, market, side, book) to
``data/closing_lines/<sport>/<season>.jsonl``.

Run on a schedule (every 30-60 minutes during game windows) so the
log accumulates pre-game prices for every market we care about. The
final pre-game snapshot per event becomes our "closing line" for CLV
analysis once the games complete.

Schema (one JSON-line per row)
------------------------------
    captured_at          UTC ISO-8601 of this snapshot
    season               Integer year
    sport                'mlb' | 'wnba' | 'nba' | 'nhl'
    sport_key            Odds API sport key
    event_id             Odds API event id (stable across snapshots)
    scheduled_start      Game's scheduled start time (UTC ISO)
    away_team / home_team
    market               'moneyline' | 'spread' | 'total'
    side                 'home' / 'away' for ML+spread, 'over'/'under' for total
    line                 None for ML; point for spread / total
    decimal_odds         Float
    american_odds        Int
    book                 Book identifier (fanduel / draftkings / etc.)

The schema is intentionally flat so analysis tools can read it as CSV
or DataFrame without nested unpacking.

Dedup
-----
Within one snapshot run, a row is unique on
(event_id, market, side, line, book). Across runs the file is
append-only -- the same row repeats with a fresh `captured_at` if the
price is unchanged. Intentional: we want a time-series of prices, not
just the latest snapshot.

Costs
-----
Each Odds API call is 1 credit per (region * market). With regions=us
+ markets=h2h,spreads,totals -> 3 credits per call. Schedule judiciously
to stay inside the API plan budget.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "closing_lines"


# Map our internal sport name to the Odds API sport key.
SPORT_KEYS: Dict[str, str] = {
    "mlb":  "baseball_mlb",
    "wnba": "basketball_wnba",
    "nba":  "basketball_nba",
    "nhl":  "icehockey_nhl",
}


def _resolve_api_key(api_key: Optional[str]) -> str:
    if api_key:
        return api_key
    for var in ("ODDS_API_KEY", "THE_ODDS_API_KEY"):
        v = os.environ.get(var)
        if v:
            return v
    raise RuntimeError(
        "Odds API key not set. Provide api_key= or export ODDS_API_KEY."
    )


def fetch_odds_payload(
    sport: str,
    api_key: Optional[str] = None,
    regions: str = "us",
    markets: str = "h2h,spreads,totals",
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """Hit the Odds API /odds endpoint for `sport`. Returns the raw
    list of event dicts (the API's top-level shape)."""
    if sport not in SPORT_KEYS:
        raise ValueError(
            f"Unknown sport '{sport}'. Known: {sorted(SPORT_KEYS)}"
        )
    sport_key = SPORT_KEYS[sport]
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
        f"?regions={regions}&markets={markets}&oddsFormat=american"
    )
    resp = requests.get(
        url, params={"apiKey": _resolve_api_key(api_key)},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json() or []


# ---------------------------------------------------------------------
# Normalisation: nested API payload -> flat snapshot rows
# ---------------------------------------------------------------------

_MARKET_LABEL: Dict[str, str] = {
    "h2h":     "moneyline",
    "spreads": "spread",
    "totals":  "total",
}


def _american_to_decimal(american: float) -> float:
    """Standard conversion. -110 -> 1.909; +120 -> 2.20."""
    a = float(american)
    if a == 0:
        return 1.0
    return round(1.0 + (100.0 / abs(a) if a < 0 else a / 100.0), 4)


def _normalize_side_label(market: str, name: str, home: str, away: str) -> str:
    """Map outcome `name` to our canonical side label."""
    if market in ("h2h", "spreads"):
        if name == home:
            return "home"
        if name == away:
            return "away"
        return name.lower().replace(" ", "_")
    if market == "totals":
        n = (name or "").strip().lower()
        return n if n in ("over", "under") else n
    return name.lower()


def _season_from_commence(iso: Optional[str]) -> Optional[int]:
    """Year the snapshot belongs to. Calendar-year for MLB / WNBA;
    callers needing season-spanning logic for NBA / NHL can refine
    downstream from `scheduled_start`."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.year


def normalize_payload(
    payload: List[Dict[str, Any]],
    *, sport: str, captured_at: str,
) -> List[Dict[str, Any]]:
    """Iterate the Odds API payload and yield flat snapshot rows.

    Skips events without parseable schedule + bookmaker pair. Never
    raises -- a bad event is dropped silently so one rotten row
    doesn't poison a 30-minute snapshot.
    """
    rows: List[Dict[str, Any]] = []
    sport_key = SPORT_KEYS.get(sport, sport)
    for event in payload or []:
        event_id = event.get("id")
        commence = event.get("commence_time")
        away = event.get("away_team")
        home = event.get("home_team")
        if not (event_id and away and home):
            continue
        season = _season_from_commence(commence)
        for book in event.get("bookmakers") or []:
            book_key = book.get("key") or book.get("title") or "unknown"
            for market in book.get("markets") or []:
                m_key = market.get("key")
                m_label = _MARKET_LABEL.get(m_key)
                if not m_label:
                    continue
                for outcome in market.get("outcomes") or []:
                    name = outcome.get("name") or ""
                    am = outcome.get("price")
                    if am is None:
                        continue
                    try:
                        american = int(round(float(am)))
                    except (TypeError, ValueError):
                        continue
                    point = outcome.get("point")
                    line = float(point) if point is not None else None
                    rows.append({
                        "captured_at": captured_at,
                        "season": season,
                        "sport": sport,
                        "sport_key": sport_key,
                        "event_id": str(event_id),
                        "scheduled_start": commence,
                        "away_team": away,
                        "home_team": home,
                        "market": m_label,
                        "side": _normalize_side_label(m_key, name, home, away),
                        "line": line,
                        "decimal_odds": _american_to_decimal(american),
                        "american_odds": american,
                        "book": book_key,
                    })
    return rows


# ---------------------------------------------------------------------
# Append-only writer
# ---------------------------------------------------------------------

def append_snapshot(
    rows: Iterable[Dict[str, Any]],
    *, output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Dict[str, Any]:
    """Append snapshot rows to ``<output_dir>/<sport>/<season>.jsonl``.

    Rows missing sport / season are dropped. Rows for the same season
    land in one file with a single open + fsync per (sport, season).
    Returns counts written per file.
    """
    by_path: Dict[Path, List[Dict[str, Any]]] = {}
    skipped = 0
    for row in rows:
        sport = row.get("sport")
        season = row.get("season")
        if not sport or season is None:
            skipped += 1
            continue
        path = Path(output_dir) / sport / f"{season}.jsonl"
        by_path.setdefault(path, []).append(row)
    written: Dict[str, int] = {}
    for path, rs in by_path.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for r in rs:
                f.write(json.dumps(r) + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            relative = str(path.relative_to(REPO_ROOT))
        except ValueError:
            relative = str(path)
        written[relative] = len(rs)
    return {
        "total_rows": sum(written.values()),
        "skipped": skipped,
        "files": written,
    }


# ---------------------------------------------------------------------
# Top-level snapshot()
# ---------------------------------------------------------------------

@dataclass
class SnapshotResult:
    sport: str
    n_events: int = 0
    n_rows: int = 0
    n_books: int = 0
    files: Dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None


def snapshot(
    sport: str,
    *,
    api_key: Optional[str] = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    captured_at: Optional[str] = None,
    regions: str = "us",
    markets: str = "h2h,spreads,totals",
) -> SnapshotResult:
    """End-to-end: fetch -> normalise -> append. Best-effort: any
    exception inside is captured in `result.error` so a multi-sport
    cron doesn't abort the chain when one sport's API call flakes."""
    captured_at = captured_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )
    result = SnapshotResult(sport=sport)
    try:
        payload = fetch_odds_payload(
            sport, api_key=api_key, regions=regions, markets=markets,
        )
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        return result
    result.n_events = len(payload)
    result.n_books = sum(
        len(e.get("bookmakers") or []) for e in payload
    )
    rows = normalize_payload(
        payload, sport=sport, captured_at=captured_at,
    )
    result.n_rows = len(rows)
    if rows:
        stats = append_snapshot(rows, output_dir=output_dir)
        result.files = stats["files"]
    return result
