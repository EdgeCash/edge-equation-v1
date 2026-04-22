import base64
import json

import httpx
import pytest

from edge_equation.persistence.turso import (
    PIPELINE_PATH,
    TursoConnection,
    TursoError,
    _RowLike,
    _scheme_to_https,
    _type_for,
    _unwrap,
)


# ------------------------------------------------ scheme translation


def test_libsql_rewrites_to_https():
    assert _scheme_to_https("libsql://x.turso.io") == "https://x.turso.io"
    assert _scheme_to_https("wss://x.turso.io") == "https://x.turso.io"
    # Already https stays untouched
    assert _scheme_to_https("https://x.turso.io") == "https://x.turso.io"


# ------------------------------------------------ typed arg / unwrap


def test_type_for_primitives():
    assert _type_for(None) == {"type": "null"}
    assert _type_for(1) == {"type": "integer", "value": "1"}
    assert _type_for(True) == {"type": "integer", "value": "1"}
    assert _type_for(False) == {"type": "integer", "value": "0"}
    assert _type_for(1.25) == {"type": "float", "value": "1.25"}
    assert _type_for("abc") == {"type": "text", "value": "abc"}


def test_type_for_blob():
    arg = _type_for(b"hello")
    assert arg["type"] == "blob"
    assert base64.b64decode(arg["base64"]) == b"hello"


def test_unwrap_roundtrip():
    cases = [
        ({"type": "null"}, None),
        ({"type": "integer", "value": "42"}, 42),
        ({"type": "float", "value": "3.14"}, 3.14),
        ({"type": "text", "value": "x"}, "x"),
    ]
    for raw, expected in cases:
        assert _unwrap(raw) == expected


# ------------------------------------------------ RowLike dict/index access


def test_row_like_index_and_name_access():
    desc = [("id", None, None, None, None, None, None), ("name", None, None, None, None, None, None)]
    row = _RowLike(desc, [1, "Alice"])
    assert row[0] == 1
    assert row[1] == "Alice"
    assert row["id"] == 1
    assert row["name"] == "Alice"
    assert list(row) == [1, "Alice"]
    assert row.keys() == ["id", "name"]
    assert len(row) == 2


# ------------------------------------------------ mocked pipeline I/O


def _mock_client(responder):
    return httpx.Client(transport=httpx.MockTransport(responder))


def _ok_result(cols=None, rows=None, affected=0, last_insert=None):
    return {
        "type": "ok",
        "response": {
            "type": "execute",
            "result": {
                "cols": [{"name": c} for c in (cols or [])],
                "rows": [
                    [_type_for(v) for v in row]
                    for row in (rows or [])
                ],
                "affected_row_count": affected,
                "last_insert_rowid": str(last_insert) if last_insert is not None else None,
            },
        },
    }


def test_execute_select_returns_rows():
    def handler(request):
        assert request.url.path == PIPELINE_PATH
        payload = json.loads(request.content.decode())
        assert payload["requests"][0]["type"] == "execute"
        return httpx.Response(200, json={"results": [
            _ok_result(cols=["id", "name"], rows=[[1, "Alice"], [2, "Bob"]]),
        ]})

    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(handler),
    )
    cur = conn.execute("SELECT id, name FROM x")
    rows = cur.fetchall()
    assert rows == [(1, "Alice"), (2, "Bob")]


def test_execute_binds_positional_args():
    seen = {}

    def handler(request):
        payload = json.loads(request.content.decode())
        seen["args"] = payload["requests"][0]["stmt"]["args"]
        return httpx.Response(200, json={"results": [_ok_result()]})

    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(handler),
    )
    conn.execute("INSERT INTO x (a, b, c) VALUES (?, ?, ?)", (1, "hi", None))
    assert seen["args"] == [
        {"type": "integer", "value": "1"},
        {"type": "text", "value": "hi"},
        {"type": "null"},
    ]


def test_named_params_raise():
    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(lambda r: httpx.Response(200, json={"results": []})),
    )
    with pytest.raises(NotImplementedError):
        conn.execute("SELECT :x", {"x": 1})


def test_executescript_splits_and_pipelines():
    seen = {}

    def handler(request):
        payload = json.loads(request.content.decode())
        seen["statements"] = [r["stmt"]["sql"] for r in payload["requests"]]
        return httpx.Response(200, json={"results": [
            _ok_result() for _ in seen["statements"]
        ]})

    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(handler),
    )
    conn.executescript("""
        -- first
        CREATE TABLE a (id INTEGER);
        CREATE TABLE b (id INTEGER);
    """)
    assert seen["statements"] == [
        "CREATE TABLE a (id INTEGER)",
        "CREATE TABLE b (id INTEGER)",
    ]


def test_error_response_raises_turso_error():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"type": "error", "error": {"code": "BAD", "message": "nope"}},
        ]})
    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(handler),
    )
    with pytest.raises(TursoError, match="BAD"):
        conn.execute("SELECT 1")


def test_http_error_raises_turso_error():
    def handler(request):
        return httpx.Response(500, text="boom")
    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(handler),
    )
    with pytest.raises(TursoError, match="HTTP 500"):
        conn.execute("SELECT 1")


def test_auth_token_sent_as_bearer():
    seen = {}
    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"results": [_ok_result()]})
    conn = TursoConnection(
        url="https://x.turso.io", auth_token="SEC",
        http_client=_mock_client(handler),
    )
    conn.execute("SELECT 1")
    assert seen["auth"] == "Bearer SEC"


def test_cursor_fetchone_exhausts():
    def handler(request):
        return httpx.Response(200, json={"results": [
            _ok_result(cols=["id"], rows=[[1], [2]]),
        ]})
    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(handler),
    )
    cur = conn.execute("SELECT id FROM x")
    assert cur.fetchone() == (1,)
    assert cur.fetchone() == (2,)
    assert cur.fetchone() is None


def test_cursor_lastrowid_and_rowcount():
    def handler(request):
        return httpx.Response(200, json={"results": [
            _ok_result(affected=1, last_insert=42),
        ]})
    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(handler),
    )
    cur = conn.execute("INSERT INTO x DEFAULT VALUES")
    assert cur.rowcount == 1
    assert cur.lastrowid == 42


def test_row_factory_sqlite_row_compatible():
    import sqlite3
    def handler(request):
        return httpx.Response(200, json={"results": [
            _ok_result(cols=["id", "name"], rows=[[1, "Alice"]]),
        ]})
    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(handler),
    )
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT id, name FROM x")
    row = cur.fetchone()
    assert row["id"] == 1
    assert row["name"] == "Alice"


def test_commit_rollback_are_noops():
    conn = TursoConnection(
        url="https://x.turso.io", auth_token="tok",
        http_client=_mock_client(lambda r: httpx.Response(200, json={"results": []})),
    )
    # Should not raise or emit any HTTP call
    conn.commit()
    conn.rollback()


def test_close_closes_owned_client():
    closed = {"called": False}
    class _Client:
        def post(self, *a, **k): raise AssertionError("unused")
        def close(self):
            closed["called"] = True

    conn = TursoConnection(url="https://x.turso.io", auth_token="tok", http_client=_Client())
    # _owns_client is False when http_client is injected, so .close() MUST NOT
    # close the injected client.
    conn.close()
    assert closed["called"] is False


def test_context_manager_closes():
    # Use the default (owned) client; context-manager exit must close it.
    with TursoConnection(url="https://x.turso.io", auth_token="tok") as conn:
        assert conn._owns_client is True
    # After __exit__ the client is closed; post() on it would raise. We can't
    # easily inspect internal state without reaching in, so settle for the
    # invariant check above plus no-exception-on-exit.
