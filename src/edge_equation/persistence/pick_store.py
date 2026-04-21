"""
PickStore: persistence of engine-produced Picks.

Each row is an append-only snapshot of a Pick at the time it was generated.
Decimal fields round-trip as strings; kelly_breakdown and metadata round-trip
as JSON blobs. The stored record carries a DB-assigned integer id (pick_id)
in addition to the Pick's business identity (game_id + market_type + selection).

Writes are via insert(). Reads return PickRecord, a pure data envelope that
mirrors Pick plus pick_id + slate_id + recorded_at. Conversion back into a
Pick is a single call to to_pick().
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import json
import sqlite3
from typing import Any, Dict, List, Optional

from edge_equation.engine.pick_schema import Pick, Line


@dataclass(frozen=True)
class PickRecord:
    """Stored representation of a Pick plus DB bookkeeping fields."""
    pick_id: int
    slate_id: Optional[str]
    recorded_at: str
    sport: str
    market_type: str
    selection: str
    odds: int
    line_number: Optional[Decimal]
    fair_prob: Optional[Decimal]
    expected_value: Optional[Decimal]
    edge: Optional[Decimal]
    kelly: Optional[Decimal]
    grade: str
    realization: int
    game_id: Optional[str]
    event_time: Optional[str]
    decay_halflife_days: Optional[Decimal]
    hfa_value: Optional[Decimal]
    kelly_breakdown: Optional[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_pick(self) -> Pick:
        return Pick(
            sport=self.sport,
            market_type=self.market_type,
            selection=self.selection,
            line=Line(odds=self.odds, number=self.line_number),
            fair_prob=self.fair_prob,
            expected_value=self.expected_value,
            edge=self.edge,
            kelly=self.kelly,
            grade=self.grade,
            realization=self.realization,
            game_id=self.game_id,
            event_time=self.event_time,
            decay_halflife_days=self.decay_halflife_days,
            hfa_value=self.hfa_value,
            kelly_breakdown=self.kelly_breakdown,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict:
        return {
            "pick_id": self.pick_id,
            "slate_id": self.slate_id,
            "recorded_at": self.recorded_at,
            **self.to_pick().to_dict(),
        }


class PickStore:
    """
    CRUD for Pick rows:
    - insert(conn, pick, slate_id=None, recorded_at=None) -> pick_id
    - insert_many(conn, picks, slate_id=None, recorded_at=None) -> list[int]
    - get(conn, pick_id)                   -> PickRecord or None
    - list_by_slate(conn, slate_id)        -> list[PickRecord]
    - list_by_game(conn, game_id)          -> list[PickRecord]
    - list_by_sport(conn, sport, limit=50) -> list[PickRecord]
    - update_realization(conn, pick_id, realization) -> int (rows updated)
    """

    @staticmethod
    def _iso(ts: Any) -> str:
        if ts is None:
            return datetime.utcnow().isoformat()
        if isinstance(ts, datetime):
            return ts.isoformat()
        return str(ts)

    @staticmethod
    def _dec_str(v: Optional[Decimal]) -> Optional[str]:
        return str(v) if v is not None else None

    @staticmethod
    def _str_dec(v: Optional[str]) -> Optional[Decimal]:
        return Decimal(v) if v is not None and v != "" else None

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> PickRecord:
        meta_raw = row["metadata_json"]
        metadata = json.loads(meta_raw) if meta_raw else {}
        kb_raw = row["kelly_breakdown_json"]
        kelly_breakdown = json.loads(kb_raw) if kb_raw else None
        return PickRecord(
            pick_id=int(row["id"]),
            slate_id=row["slate_id"],
            recorded_at=row["recorded_at"],
            sport=row["sport"],
            market_type=row["market_type"],
            selection=row["selection"],
            odds=int(row["odds"]),
            line_number=PickStore._str_dec(row["line_number"]),
            fair_prob=PickStore._str_dec(row["fair_prob"]),
            expected_value=PickStore._str_dec(row["expected_value"]),
            edge=PickStore._str_dec(row["edge"]),
            kelly=PickStore._str_dec(row["kelly"]),
            grade=row["grade"],
            realization=int(row["realization"]) if row["realization"] is not None else 47,
            game_id=row["game_id"],
            event_time=row["event_time"],
            decay_halflife_days=PickStore._str_dec(row["decay_halflife_days"]),
            hfa_value=PickStore._str_dec(row["hfa_value"]),
            kelly_breakdown=kelly_breakdown,
            metadata=metadata,
        )

    @staticmethod
    def insert(
        conn: sqlite3.Connection,
        pick: Pick,
        slate_id: Optional[str] = None,
        recorded_at: Optional[Any] = None,
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO picks (
                slate_id, game_id, sport, market_type, selection,
                odds, line_number, fair_prob, expected_value, edge, kelly,
                grade, realization, decay_halflife_days, hfa_value,
                kelly_breakdown_json, event_time, metadata_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slate_id,
                pick.game_id,
                pick.sport,
                pick.market_type,
                pick.selection,
                int(pick.line.odds),
                PickStore._dec_str(pick.line.number),
                PickStore._dec_str(pick.fair_prob),
                PickStore._dec_str(pick.expected_value),
                PickStore._dec_str(pick.edge),
                PickStore._dec_str(pick.kelly),
                pick.grade,
                int(pick.realization),
                PickStore._dec_str(pick.decay_halflife_days),
                PickStore._dec_str(pick.hfa_value),
                json.dumps(pick.kelly_breakdown) if pick.kelly_breakdown is not None else None,
                pick.event_time,
                json.dumps(pick.metadata) if pick.metadata else None,
                PickStore._iso(recorded_at),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def insert_many(
        conn: sqlite3.Connection,
        picks: List[Pick],
        slate_id: Optional[str] = None,
        recorded_at: Optional[Any] = None,
    ) -> List[int]:
        ids: List[int] = []
        for p in picks:
            ids.append(PickStore.insert(conn, p, slate_id=slate_id, recorded_at=recorded_at))
        return ids

    @staticmethod
    def get(conn: sqlite3.Connection, pick_id: int) -> Optional[PickRecord]:
        row = conn.execute("SELECT * FROM picks WHERE id = ?", (int(pick_id),)).fetchone()
        if row is None:
            return None
        return PickStore._row_to_record(row)

    @staticmethod
    def list_by_slate(conn: sqlite3.Connection, slate_id: str) -> List[PickRecord]:
        rows = conn.execute(
            "SELECT * FROM picks WHERE slate_id = ? ORDER BY id ASC",
            (slate_id,),
        ).fetchall()
        return [PickStore._row_to_record(r) for r in rows]

    @staticmethod
    def list_by_game(conn: sqlite3.Connection, game_id: str) -> List[PickRecord]:
        rows = conn.execute(
            "SELECT * FROM picks WHERE game_id = ? ORDER BY id ASC",
            (game_id,),
        ).fetchall()
        return [PickStore._row_to_record(r) for r in rows]

    @staticmethod
    def list_by_sport(
        conn: sqlite3.Connection,
        sport: str,
        limit: int = 50,
    ) -> List[PickRecord]:
        rows = conn.execute(
            "SELECT * FROM picks WHERE sport = ? ORDER BY recorded_at DESC LIMIT ?",
            (sport, int(limit)),
        ).fetchall()
        return [PickStore._row_to_record(r) for r in rows]

    @staticmethod
    def update_realization(
        conn: sqlite3.Connection,
        pick_id: int,
        realization: int,
    ) -> int:
        cur = conn.execute(
            "UPDATE picks SET realization = ? WHERE id = ?",
            (int(realization), int(pick_id)),
        )
        conn.commit()
        return cur.rowcount
