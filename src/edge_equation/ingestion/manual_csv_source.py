"""
ManualCsvSource: a CSV loader for leagues The Odds API free tier does not
cover (KBO, NPB) or any slate you want to hand-load.

CSV schema (flat; one row per game x market x selection):

    league,game_id,start_time,home_team,away_team,market_type,selection,line,odds

Columns:
- league       Internal league code (KBO, NPB, MLB, NHL, ...).
- game_id      Stable unique id. Convention: "{LEAGUE}-{YYYY-MM-DD}-{AWAY}-{HOME}".
- start_time   ISO-8601 datetime with timezone offset (e.g. 2026-04-20T18:30:00+09:00).
- home_team    Home team short code or full name.
- away_team    Away team short code or full name.
- market_type  Internal market type (ML, Run_Line, Total, Over, K, HR, NRFI, YRFI, ...).
- selection    Side / over-under name, or full prop text. Blank is an error.
- line         Decimal line for spread / total / prop O/U. Empty cell for ML.
- odds         Signed American integer (+120, -150). Empty cell means "no price".

Game-level columns are DUPLICATED across all rows for a single game. The
loader groups by game_id so each unique game becomes one raw game dict, and
every row becomes one raw market dict -- matching the shape expected by
ingestion.normalizer.normalize_slate.

Typos, unknown leagues, or unknown market types pass through untouched at
load time; validation happens in the downstream normalizer.
"""
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional


REQUIRED_COLUMNS = (
    "league",
    "game_id",
    "start_time",
    "home_team",
    "away_team",
    "market_type",
    "selection",
    "line",
    "odds",
)


class ManualCsvSource:
    """
    CSV-backed IngestionSource:
    - __init__(csv_path)                               -> validates file exists
    - get_raw_games(run_datetime=None)                 -> list of raw game dicts
    - get_raw_markets(run_datetime=None)               -> list of raw market dicts
    File is re-read on every call so weekly edits don't require reconstruction.
    """

    def __init__(self, csv_path: str):
        self.csv_path = Path(csv_path)
        if not self.csv_path.exists():
            raise FileNotFoundError(f"CSV not found: {csv_path}")

    def _read_rows(self) -> List[dict]:
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
            if missing:
                raise ValueError(
                    f"CSV {self.csv_path} missing required columns: {missing}"
                )
            return [dict(r) for r in reader]

    @staticmethod
    def _clean(s: Optional[str]) -> str:
        return (s or "").strip()

    def get_raw_games(self, run_datetime: Optional[datetime] = None) -> list:
        rows = self._read_rows()
        seen = {}
        order = []
        for r in rows:
            gid = ManualCsvSource._clean(r.get("game_id"))
            if not gid:
                raise ValueError(f"CSV {self.csv_path} has row with empty game_id")
            if gid not in seen:
                seen[gid] = {
                    "league": ManualCsvSource._clean(r.get("league")),
                    "game_id": gid,
                    "start_time": ManualCsvSource._clean(r.get("start_time")),
                    "home_team": ManualCsvSource._clean(r.get("home_team")),
                    "away_team": ManualCsvSource._clean(r.get("away_team")),
                }
                order.append(gid)
        return [seen[g] for g in order]

    def get_raw_markets(self, run_datetime: Optional[datetime] = None) -> list:
        rows = self._read_rows()
        markets = []
        for r in rows:
            line_raw = ManualCsvSource._clean(r.get("line"))
            odds_raw = ManualCsvSource._clean(r.get("odds"))
            selection = ManualCsvSource._clean(r.get("selection"))
            if not selection:
                raise ValueError(
                    f"CSV {self.csv_path} has row with empty selection for "
                    f"game_id={r.get('game_id')!r}"
                )
            markets.append({
                "game_id": ManualCsvSource._clean(r.get("game_id")),
                "market_type": ManualCsvSource._clean(r.get("market_type")),
                "selection": selection,
                "line": Decimal(line_raw) if line_raw else None,
                "odds": int(odds_raw) if odds_raw else None,
                "meta": {"source": "manual_csv", "path": str(self.csv_path)},
            })
        return markets
