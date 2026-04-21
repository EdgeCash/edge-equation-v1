"""
Game-result schema plus SQLite persistence.

A GameResult is a completed (final) game with scores attached. It's what the
stats layer consumes to produce ratings and feature inputs.

The game_results table was added in migration v2. It's keyed by game_id
(UNIQUE) so recording a result for the same game twice upserts rather than
duplicating. Indexes on league / start_time / home_team / away_team keep
the rolling-window queries cheap.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import sqlite3
from typing import Any, List, Optional


VALID_STATUS = ("final", "forfeit", "suspended")


@dataclass(frozen=True)
class GameResult:
    """One completed game row from the stats store."""
    result_id: Optional[int]
    game_id: str
    league: str
    home_team: str
    away_team: str
    start_time: str
    home_score: int
    away_score: int
    status: str = "final"
    recorded_at: Optional[str] = None

    def home_won(self) -> bool:
        return self.home_score > self.away_score

    def is_draw(self) -> bool:
        return self.home_score == self.away_score

    def total(self) -> int:
        return self.home_score + self.away_score

    def margin(self) -> int:
        """Home margin (positive = home won)."""
        return self.home_score - self.away_score

    def to_dict(self) -> dict:
        return {
            "result_id": self.result_id,
            "game_id": self.game_id,
            "league": self.league,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "start_time": self.start_time,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "status": self.status,
            "recorded_at": self.recorded_at,
        }


class GameResultsStore:
    """
    CRUD for game_results rows:
    - record(conn, result)                  -> row id (upsert on game_id)
    - record_many(conn, results)            -> list of ids
    - get(conn, game_id)                    -> GameResult or None
    - list_by_league(conn, league, limit)   -> list[GameResult] ordered desc by start_time
    - list_for_team(conn, league, team, limit) -> list[GameResult]
    - list_between(conn, league, start_iso, end_iso) -> list[GameResult]
    - count_by_league(conn, league)         -> int
    """

    @staticmethod
    def _iso(ts: Any) -> str:
        if ts is None:
            return datetime.utcnow().isoformat()
        if isinstance(ts, datetime):
            return ts.isoformat()
        return str(ts)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> GameResult:
        return GameResult(
            result_id=int(row["id"]),
            game_id=row["game_id"],
            league=row["league"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            start_time=row["start_time"],
            home_score=int(row["home_score"]),
            away_score=int(row["away_score"]),
            status=row["status"],
            recorded_at=row["recorded_at"],
        )

    @staticmethod
    def record(conn: sqlite3.Connection, result: GameResult) -> int:
        if result.status not in VALID_STATUS:
            raise ValueError(
                f"status must be one of {VALID_STATUS}, got {result.status!r}"
            )
        recorded_at = GameResultsStore._iso(result.recorded_at)
        conn.execute(
            """
            INSERT INTO game_results
                (game_id, league, home_team, away_team, start_time,
                 home_score, away_score, status, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id) DO UPDATE SET
                home_score = excluded.home_score,
                away_score = excluded.away_score,
                status = excluded.status,
                recorded_at = excluded.recorded_at
            """,
            (
                result.game_id, result.league, result.home_team, result.away_team,
                str(result.start_time), int(result.home_score), int(result.away_score),
                result.status, recorded_at,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM game_results WHERE game_id = ?",
            (result.game_id,),
        ).fetchone()
        return int(row["id"])

    @staticmethod
    def record_many(conn: sqlite3.Connection, results: List[GameResult]) -> List[int]:
        return [GameResultsStore.record(conn, r) for r in results]

    @staticmethod
    def get(conn: sqlite3.Connection, game_id: str) -> Optional[GameResult]:
        row = conn.execute(
            "SELECT * FROM game_results WHERE game_id = ?", (game_id,),
        ).fetchone()
        return GameResultsStore._row_to_record(row) if row else None

    @staticmethod
    def list_by_league(
        conn: sqlite3.Connection, league: str, limit: int = 200,
    ) -> List[GameResult]:
        rows = conn.execute(
            "SELECT * FROM game_results WHERE league = ? "
            "ORDER BY start_time DESC LIMIT ?",
            (league, int(limit)),
        ).fetchall()
        return [GameResultsStore._row_to_record(r) for r in rows]

    @staticmethod
    def list_for_team(
        conn: sqlite3.Connection,
        league: str,
        team: str,
        limit: int = 50,
    ) -> List[GameResult]:
        rows = conn.execute(
            """
            SELECT * FROM game_results
            WHERE league = ? AND (home_team = ? OR away_team = ?)
            ORDER BY start_time DESC LIMIT ?
            """,
            (league, team, team, int(limit)),
        ).fetchall()
        return [GameResultsStore._row_to_record(r) for r in rows]

    @staticmethod
    def list_between(
        conn: sqlite3.Connection,
        league: str,
        start_iso: str,
        end_iso: str,
    ) -> List[GameResult]:
        rows = conn.execute(
            """
            SELECT * FROM game_results
            WHERE league = ? AND start_time >= ? AND start_time < ?
            ORDER BY start_time ASC
            """,
            (league, start_iso, end_iso),
        ).fetchall()
        return [GameResultsStore._row_to_record(r) for r in rows]

    @staticmethod
    def count_by_league(conn: sqlite3.Connection, league: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM game_results WHERE league = ?",
            (league,),
        ).fetchone()
        return int(row["c"])
