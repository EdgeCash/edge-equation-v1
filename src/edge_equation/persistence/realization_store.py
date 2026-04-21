"""
RealizationStore: actual outcomes of graded picks.

Outcomes are keyed by (game_id, market_type, selection) -- the natural unique
key for a bet. A single game can have many market/selection rows.

Outcome values:
- "win"   : stake returned with profit
- "loss"  : stake lost
- "push"  : stake returned, no profit
- "void"  : bet cancelled / cashed out before settlement

actual_value (Decimal, optional) records the realized numeric for prop-style
markets -- e.g. total score for Over/Under, strikeout count for K props.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import sqlite3
from typing import Any, List, Optional


VALID_OUTCOMES = ("win", "loss", "push", "void")


@dataclass(frozen=True)
class OutcomeRecord:
    """One settled market outcome for a single game."""
    outcome_id: int
    game_id: str
    market_type: str
    selection: str
    outcome: str
    actual_value: Optional[Decimal]
    recorded_at: str

    def to_dict(self) -> dict:
        return {
            "outcome_id": self.outcome_id,
            "game_id": self.game_id,
            "market_type": self.market_type,
            "selection": self.selection,
            "outcome": self.outcome,
            "actual_value": str(self.actual_value) if self.actual_value is not None else None,
            "recorded_at": self.recorded_at,
        }


class RealizationStore:
    """
    Upsert-based outcome store:
    - record_outcome(conn, game_id, market_type, selection, outcome, actual_value=None, recorded_at=None)
        -> outcome_id (INSERT or UPDATE of existing row)
    - get_outcome(conn, game_id, market_type, selection) -> OutcomeRecord or None
    - list_outcomes_by_game(conn, game_id)                -> list[OutcomeRecord]
    - list_recent(conn, limit=50)                         -> list[OutcomeRecord]
    """

    @staticmethod
    def _iso(ts: Any) -> str:
        if ts is None:
            return datetime.utcnow().isoformat()
        if isinstance(ts, datetime):
            return ts.isoformat()
        return str(ts)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> OutcomeRecord:
        av = row["actual_value"]
        return OutcomeRecord(
            outcome_id=int(row["id"]),
            game_id=row["game_id"],
            market_type=row["market_type"],
            selection=row["selection"],
            outcome=row["outcome"],
            actual_value=Decimal(av) if av is not None else None,
            recorded_at=row["recorded_at"],
        )

    @staticmethod
    def record_outcome(
        conn: sqlite3.Connection,
        game_id: str,
        market_type: str,
        selection: str,
        outcome: str,
        actual_value: Optional[Decimal] = None,
        recorded_at: Optional[Any] = None,
    ) -> int:
        if outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {VALID_OUTCOMES}, got {outcome!r}"
            )
        av_str = str(actual_value) if actual_value is not None else None
        ts = RealizationStore._iso(recorded_at)
        conn.execute(
            """
            INSERT INTO realizations
                (game_id, market_type, selection, outcome, actual_value, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, market_type, selection) DO UPDATE SET
                outcome = excluded.outcome,
                actual_value = excluded.actual_value,
                recorded_at = excluded.recorded_at
            """,
            (game_id, market_type, selection, outcome, av_str, ts),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM realizations WHERE game_id = ? AND market_type = ? AND selection = ?",
            (game_id, market_type, selection),
        ).fetchone()
        return int(row["id"])

    @staticmethod
    def get_outcome(
        conn: sqlite3.Connection,
        game_id: str,
        market_type: str,
        selection: str,
    ) -> Optional[OutcomeRecord]:
        row = conn.execute(
            "SELECT * FROM realizations WHERE game_id = ? AND market_type = ? AND selection = ?",
            (game_id, market_type, selection),
        ).fetchone()
        if row is None:
            return None
        return RealizationStore._row_to_record(row)

    @staticmethod
    def list_outcomes_by_game(
        conn: sqlite3.Connection,
        game_id: str,
    ) -> List[OutcomeRecord]:
        rows = conn.execute(
            "SELECT * FROM realizations WHERE game_id = ? ORDER BY id ASC",
            (game_id,),
        ).fetchall()
        return [RealizationStore._row_to_record(r) for r in rows]

    @staticmethod
    def list_recent(
        conn: sqlite3.Connection,
        limit: int = 50,
    ) -> List[OutcomeRecord]:
        rows = conn.execute(
            "SELECT * FROM realizations ORDER BY recorded_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [RealizationStore._row_to_record(r) for r in rows]
