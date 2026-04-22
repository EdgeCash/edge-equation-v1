"""
End-to-end integration: the Turso adapter paired with an in-memory SQLite
backend, driven through the real PickStore / SlateStore / RealizationStore
APIs. This is the closest thing to a live Turso test we can run locally:
every HTTP call goes through a mock transport that translates pipeline
requests into sqlite3 operations and responds in Turso's wire format.

If the adapter is correct, every existing persistence test that passes
against sqlite3 will also pass against Turso through this emulator.
"""
from decimal import Decimal
import json
import sqlite3

import httpx
import pytest

from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.persistence.db import Database, _is_turso_path
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.persistence.turso import TursoConnection


def _py_value(arg: dict):
    """Convert a Turso typed arg dict back into a Python value."""
    t = arg.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(arg["value"])
    if t == "float":
        return float(arg["value"])
    return arg.get("value")


def _turso_value(v):
    """Serialise a Python value into a Turso typed cell for a response."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": str(v)}
    return {"type": "text", "value": str(v)}


def _emulator_transport():
    """
    Return an httpx.MockTransport backed by an in-memory SQLite DB. Every
    pipeline request is translated into a real sqlite3 operation. Responses
    use the same cols/rows/affected/lastrowid shape the Turso v2 API emits.
    """
    backend = sqlite3.connect(":memory:")
    backend.row_factory = sqlite3.Row
    backend.execute("PRAGMA foreign_keys = ON")

    def _run_one(stmt: dict) -> dict:
        sql = stmt.get("sql", "")
        args = [_py_value(a) for a in stmt.get("args") or []]
        cur = backend.execute(sql, args)
        backend.commit()
        cols: list = []
        rows: list = []
        if cur.description:
            cols = [c[0] for c in cur.description]
            fetched = cur.fetchall()
            rows = [[_turso_value(v) for v in row] for row in fetched]
        return {
            "type": "ok",
            "response": {
                "type": "execute",
                "result": {
                    "cols": [{"name": c} for c in cols],
                    "rows": rows,
                    "affected_row_count": cur.rowcount if cur.rowcount >= 0 else 0,
                    "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid else None,
                },
            },
        }

    def handler(request):
        payload = json.loads(request.content.decode())
        results = []
        for req in payload.get("requests", []):
            if req.get("type") != "execute":
                results.append({"type": "ok"})
                continue
            try:
                results.append(_run_one(req.get("stmt") or {}))
            except Exception as e:
                results.append({"type": "error", "error": {
                    "code": "BACKEND_ERROR", "message": str(e),
                }})
        return httpx.Response(200, json={"results": results})

    return httpx.MockTransport(handler)


@pytest.fixture
def turso_conn():
    client = httpx.Client(transport=_emulator_transport())
    conn = TursoConnection(
        url="https://mock-db.turso.io",
        auth_token="test-token",
        http_client=client,
    )
    conn.row_factory = sqlite3.Row
    Database.migrate(conn)
    yield conn
    conn.close()
    client.close()


# ------------------------------------------------ URL dispatch


def test_is_turso_path_recognises_url_schemes():
    assert _is_turso_path("libsql://x.turso.io") is True
    assert _is_turso_path("wss://x.turso.io") is True
    assert _is_turso_path("https://x.turso.io") is True
    assert _is_turso_path("http://localhost:8080") is True
    assert _is_turso_path("edge_equation.db") is False
    assert _is_turso_path("/tmp/some/path.db") is False
    assert _is_turso_path(":memory:") is False


def test_open_with_url_returns_turso_connection(monkeypatch):
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "secret")
    conn = Database.open(
        "libsql://mock-db.turso.io",
        http_client=httpx.Client(transport=_emulator_transport()),
    )
    assert isinstance(conn, TursoConnection)
    assert conn.auth_token == "secret"
    conn.close()


def test_open_with_plain_path_returns_sqlite(tmp_path):
    conn = Database.open(str(tmp_path / "local.db"))
    import sqlite3 as _sq
    assert isinstance(conn, _sq.Connection)
    conn.close()


# ------------------------------------------------ migrations


def test_migrations_apply_over_turso(turso_conn):
    assert Database.current_version(turso_conn) > 0
    # Every table that Phase 8 + Phase 14 migrations create should exist.
    rows = turso_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    for t in ("slates", "picks", "odds_cache", "realizations",
              "game_results", "schema_migrations"):
        assert t in names


def test_migrate_idempotent_over_turso(turso_conn):
    n = Database.migrate(turso_conn)
    assert n == 0


# ------------------------------------------------ real store round-trips


def test_slate_store_roundtrip_over_turso(turso_conn):
    SlateStore.insert(turso_conn, SlateRecord(
        slate_id="turso_slate_1",
        generated_at="2026-04-20T09:00:00",
        sport="MLB", card_type="daily_edge",
        metadata={"leagues": ["MLB", "KBO"]},
    ))
    got = SlateStore.get(turso_conn, "turso_slate_1")
    assert got is not None
    assert got.slate_id == "turso_slate_1"
    assert got.metadata == {"leagues": ["MLB", "KBO"]}


def test_pick_store_roundtrip_over_turso(turso_conn):
    SlateStore.insert(turso_conn, SlateRecord(
        slate_id="turso_slate_2",
        generated_at="2026-04-20T09:00:00",
        sport="MLB", card_type="daily_edge",
    ))
    pick = Pick(
        sport="MLB", market_type="ML", selection="BOS",
        line=Line(odds=-132),
        fair_prob=Decimal('0.553412'),
        edge=Decimal('0.022134'),
        kelly=Decimal('0.0085'),
        grade="B", realization=52,
        game_id="MLB-TURSO-1",
        decay_halflife_days=Decimal('277.258872'),
        hfa_value=Decimal('0.400000'),
        metadata={"source": "turso_e2e"},
    )
    pid = PickStore.insert(turso_conn, pick, slate_id="turso_slate_2")
    rec = PickStore.get(turso_conn, pid)
    assert rec is not None
    assert rec.fair_prob == Decimal('0.553412')
    assert rec.grade == "B"
    assert rec.decay_halflife_days == Decimal('277.258872')
    rebuilt = rec.to_pick()
    assert rebuilt.line.odds == -132
    assert rebuilt.metadata["source"] == "turso_e2e"


def test_list_by_slate_over_turso(turso_conn):
    SlateStore.insert(turso_conn, SlateRecord(
        slate_id="t_multi", generated_at="2026-04-20T09:00",
        sport=None, card_type="daily_edge",
    ))
    for i in range(3):
        PickStore.insert(turso_conn, Pick(
            sport="MLB", market_type="ML", selection=f"T{i}",
            line=Line(odds=-120), grade="C",
            fair_prob=Decimal('0.5'),
            game_id=f"G{i}",
        ), slate_id="t_multi")
    rows = PickStore.list_by_slate(turso_conn, "t_multi")
    assert len(rows) == 3
    assert sorted(r.selection for r in rows) == ["T0", "T1", "T2"]


def test_update_realization_over_turso(turso_conn):
    SlateStore.insert(turso_conn, SlateRecord(
        slate_id="t_upd", generated_at="2026-04-20T09:00",
        sport=None, card_type="daily_edge",
    ))
    pid = PickStore.insert(turso_conn, Pick(
        sport="MLB", market_type="ML", selection="BOS",
        line=Line(odds=-132), grade="B", realization=52,
        fair_prob=Decimal('0.55'),
        game_id="G1",
    ), slate_id="t_upd")
    n = PickStore.update_realization(turso_conn, pid, 100)
    assert n == 1
    rec = PickStore.get(turso_conn, pid)
    assert rec.realization == 100
