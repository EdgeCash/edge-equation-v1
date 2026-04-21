"""
Results CSV loader.

Weekly-drop format for leagues without an automated results feed (KBO,
NPB, or anything you'd rather hand-load). One row per completed game:

    league,game_id,start_time,home_team,away_team,home_score,away_score[,status]

status defaults to 'final' and may be omitted. Rows load into the
game_results table via GameResultsStore.record (upsert by game_id), so the
same file can be re-loaded without producing duplicates.
"""
import csv
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import sqlite3

from edge_equation.stats.results import GameResult, GameResultsStore


REQUIRED_COLUMNS = (
    "league", "game_id", "start_time",
    "home_team", "away_team",
    "home_score", "away_score",
)


class ResultsCsvLoader:
    """
    CSV-backed results loader:
    - read(path)                         -> list[GameResult] (pure parse; no DB)
    - load_file(conn, path, recorded_at) -> list[row ids]     (parses + upserts)
    """

    @staticmethod
    def _clean(s: Optional[str]) -> str:
        return (s or "").strip()

    @staticmethod
    def read(path: str) -> List[GameResult]:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Results CSV not found: {path}")
        with open(p, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
            if missing:
                raise ValueError(
                    f"Results CSV {path} missing columns: {missing}"
                )
            out: List[GameResult] = []
            for r in reader:
                game_id = ResultsCsvLoader._clean(r.get("game_id"))
                if not game_id:
                    raise ValueError(f"{path}: empty game_id row")
                out.append(GameResult(
                    result_id=None,
                    game_id=game_id,
                    league=ResultsCsvLoader._clean(r.get("league")),
                    home_team=ResultsCsvLoader._clean(r.get("home_team")),
                    away_team=ResultsCsvLoader._clean(r.get("away_team")),
                    start_time=ResultsCsvLoader._clean(r.get("start_time")),
                    home_score=int(ResultsCsvLoader._clean(r.get("home_score")) or 0),
                    away_score=int(ResultsCsvLoader._clean(r.get("away_score")) or 0),
                    status=ResultsCsvLoader._clean(r.get("status")) or "final",
                ))
        return out

    @staticmethod
    def load_file(
        conn: sqlite3.Connection,
        path: str,
        recorded_at: Optional[str] = None,
    ) -> List[int]:
        results = ResultsCsvLoader.read(path)
        ts = recorded_at or datetime.utcnow().isoformat()
        stamped = [
            GameResult(
                result_id=r.result_id, game_id=r.game_id, league=r.league,
                home_team=r.home_team, away_team=r.away_team,
                start_time=r.start_time,
                home_score=r.home_score, away_score=r.away_score,
                status=r.status, recorded_at=ts,
            )
            for r in results
        ]
        return GameResultsStore.record_many(conn, stamped)
