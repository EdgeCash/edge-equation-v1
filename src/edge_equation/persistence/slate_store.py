"""
SlateStore: persistence of slate-generation records.

A slate is a single run of the engine (daily edge, evening edge, a custom
backtest fold, etc). The slate_id is the caller's deterministic identifier
(e.g. "daily_edge_20260420_morning"); SlateStore does not generate one.

Fields:
- slate_id      PK
- generated_at  ISO-8601 timestamp (caller-supplied for determinism)
- sport         optional filter (None = mixed slate)
- card_type     e.g. "daily_edge", "evening_edge", "backtest_fold"
- metadata      free-form dict, stored as JSON
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import sqlite3
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SlateRecord:
    """Row from the slates table."""
    slate_id: str
    generated_at: str
    sport: Optional[str]
    card_type: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "slate_id": self.slate_id,
            "generated_at": self.generated_at,
            "sport": self.sport,
            "card_type": self.card_type,
            "metadata": dict(self.metadata),
        }


class SlateStore:
    """
    CRUD for slate generation records:
    - insert(conn, slate)                  -> slate_id
    - get(conn, slate_id)                  -> SlateRecord or None
    - list_recent(conn, limit=20)          -> list[SlateRecord]
    - list_by_card_type(conn, ct, limit)   -> list[SlateRecord]
    - delete(conn, slate_id)               -> int (rows deleted)
    """

    @staticmethod
    def _to_iso(ts: Any) -> str:
        if isinstance(ts, datetime):
            return ts.isoformat()
        return str(ts)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SlateRecord:
        meta_raw = row["metadata_json"]
        metadata = json.loads(meta_raw) if meta_raw else {}
        return SlateRecord(
            slate_id=row["slate_id"],
            generated_at=row["generated_at"],
            sport=row["sport"],
            card_type=row["card_type"],
            metadata=metadata,
        )

    @staticmethod
    def insert(conn: sqlite3.Connection, slate: SlateRecord) -> str:
        conn.execute(
            """
            INSERT INTO slates (slate_id, generated_at, sport, card_type, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                slate.slate_id,
                SlateStore._to_iso(slate.generated_at),
                slate.sport,
                slate.card_type,
                json.dumps(slate.metadata) if slate.metadata else None,
            ),
        )
        conn.commit()
        return slate.slate_id

    @staticmethod
    def get(conn: sqlite3.Connection, slate_id: str) -> Optional[SlateRecord]:
        row = conn.execute(
            "SELECT * FROM slates WHERE slate_id = ?", (slate_id,)
        ).fetchone()
        if row is None:
            return None
        return SlateStore._row_to_record(row)

    @staticmethod
    def list_recent(conn: sqlite3.Connection, limit: int = 20) -> List[SlateRecord]:
        rows = conn.execute(
            "SELECT * FROM slates ORDER BY generated_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [SlateStore._row_to_record(r) for r in rows]

    @staticmethod
    def list_by_card_type(
        conn: sqlite3.Connection,
        card_type: str,
        limit: int = 20,
    ) -> List[SlateRecord]:
        rows = conn.execute(
            "SELECT * FROM slates WHERE card_type = ? ORDER BY generated_at DESC LIMIT ?",
            (card_type, int(limit)),
        ).fetchall()
        return [SlateStore._row_to_record(r) for r in rows]

    @staticmethod
    def delete(conn: sqlite3.Connection, slate_id: str) -> int:
        cur = conn.execute("DELETE FROM slates WHERE slate_id = ?", (slate_id,))
        conn.commit()
        return cur.rowcount
