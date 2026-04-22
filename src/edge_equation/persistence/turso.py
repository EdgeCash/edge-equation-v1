"""
Turso (libSQL) HTTP adapter.

Exposes a subset of the stdlib sqlite3 Connection / Cursor API -- just enough
that the rest of the persistence layer (PickStore, SlateStore, OddsCache,
RealizationStore, GameResultsStore) works against a hosted Turso database
without modification.

Implementation strategy:
- httpx client (already in project deps) speaking to Turso's v2 pipeline
  endpoint: POST {url}/v2/pipeline with a list of execute / close requests
  in JSON.
- No long-lived connections. Every conn.execute() is a separate POST; there
  are no server-side sessions to manage, which fits serverless runners
  perfectly.
- Row factory support matches sqlite3: set conn.row_factory to sqlite3.Row
  or a callable(cursor, row) and fetchone / fetchall produce those objects.

Limitations:
- No multi-statement transactions. Turso autocommits each pipeline execute;
  conn.commit() is a no-op. The existing stores don't rely on transactions
  wider than a single statement (every .insert() commits immediately), so
  this is compatible.
- executescript splits on ';' and issues each statement as its own request.
  CREATE TABLE IF NOT EXISTS keeps migrations idempotent.
- Parameter binding: positional only (sqlite3.Connection.execute contract).
  Named parameters raise NotImplementedError.
"""
from dataclasses import dataclass
import re
import sqlite3
from typing import Any, Iterable, List, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx


PIPELINE_PATH = "/v2/pipeline"


def _scheme_to_https(url: str) -> str:
    """libsql:// and wss:// Turso URLs still hit the same HTTPS endpoint."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme in ("libsql", "wss"):
        parts = parts._replace(scheme="https")
    return urlunsplit(parts)


def _type_for(value: Any) -> dict:
    """Render a Python value as a Turso-typed argument."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        # Turso doesn't have bool; use integer 0/1 the same way sqlite3 does.
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        import base64
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    return {"type": "text", "value": str(value)}


def _unwrap(value: dict) -> Any:
    """Convert a Turso-typed value back to a Python native."""
    t = value.get("type")
    if t == "null":
        return None
    if t == "integer":
        v = value.get("value")
        return int(v) if v is not None else None
    if t == "float":
        v = value.get("value")
        return float(v) if v is not None else None
    if t == "blob":
        import base64
        b = value.get("base64")
        return base64.b64decode(b) if b else b""
    # text (and anything unknown) falls through as a string.
    return value.get("value")


class TursoError(RuntimeError):
    """Raised when the Turso HTTP endpoint returns an error response."""


@dataclass
class _ColumnInfo:
    name: str


class TursoCursor:
    """
    sqlite3.Cursor-compatible wrapper over one Turso execute response.

    Supports what the stores actually use:
    - execute(sql, params=()) -> self
    - fetchone() / fetchall()
    - rowcount, lastrowid, description
    - row_factory (set on the owning connection; applied on fetch)
    """

    def __init__(self, connection: "TursoConnection"):
        self._connection = connection
        self._description: List[tuple] = []
        self._rows: List[list] = []
        self._row_index = 0
        self.rowcount: int = -1
        self.lastrowid: Optional[int] = None

    @property
    def description(self) -> List[tuple]:
        return self._description

    def execute(self, sql: str, params: Sequence[Any] = ()) -> "TursoCursor":
        result = self._connection._execute_one(sql, params)
        self._ingest_result(result)
        return self

    def _ingest_result(self, result: dict) -> None:
        cols = result.get("cols") or []
        self._description = [
            # (name, type_code, display_size, internal_size, precision, scale, null_ok)
            (c.get("name") or "", None, None, None, None, None, None)
            for c in cols
        ]
        raw_rows = result.get("rows") or []
        self._rows = [[_unwrap(cell) for cell in row] for row in raw_rows]
        self._row_index = 0
        self.rowcount = int(result.get("affected_row_count") or 0)
        lri = result.get("last_insert_rowid")
        self.lastrowid = int(lri) if lri is not None else None

    def _shape_row(self, row: list) -> Any:
        factory = self._connection.row_factory
        if factory is None:
            return tuple(row)
        if factory is sqlite3.Row:
            # sqlite3.Row can't be constructed directly in pure Python; use
            # the dict-like adapter we ship.
            return _RowLike(self._description, row)
        # User-supplied callable: factory(cursor, row_tuple)
        return factory(self, tuple(row))

    def fetchone(self) -> Optional[Any]:
        if self._row_index >= len(self._rows):
            return None
        r = self._rows[self._row_index]
        self._row_index += 1
        return self._shape_row(r)

    def fetchall(self) -> list:
        out = [self._shape_row(r) for r in self._rows[self._row_index:]]
        self._row_index = len(self._rows)
        return out

    def __iter__(self):
        while True:
            r = self.fetchone()
            if r is None:
                return
            yield r


class _RowLike:
    """
    A dict-and-index-like row compatible with sqlite3.Row consumers.
    Supports row[0], row['col'], and `for k in row`.
    """
    __slots__ = ("_desc", "_values", "_idx_by_name")

    def __init__(self, description: List[tuple], values: list):
        self._desc = description
        self._values = values
        self._idx_by_name = {d[0]: i for i, d in enumerate(description)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._idx_by_name[key]]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return [d[0] for d in self._desc]


class TursoConnection:
    """
    sqlite3.Connection-compatible wrapper over Turso HTTP pipeline.

    Construct via TursoConnection.from_url(url, auth_token) or via the
    Database.open() factory in db.py, which dispatches on URL scheme.

    Methods implemented:
    - execute(sql, params=())       -> TursoCursor
    - executescript(script)         -> None
    - cursor()                      -> TursoCursor
    - commit()                      -> None   (Turso autocommits; no-op)
    - close()                       -> None
    - row_factory (attribute)

    Construction:
    - url: Turso database URL. libsql:// and wss:// are rewritten to https.
    - auth_token: Bearer token from Turso dashboard.
    - http_client: optional httpx.Client for dependency injection in tests.
    """

    def __init__(
        self,
        url: str,
        auth_token: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.url = _scheme_to_https(url).rstrip("/")
        self.auth_token = auth_token
        self.row_factory = None
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=30.0)

    @staticmethod
    def from_url(url: str, auth_token: Optional[str] = None) -> "TursoConnection":
        return TursoConnection(url=url, auth_token=auth_token)

    # ------------------------------------------------- low-level HTTP

    def _pipeline(self, requests: list) -> list:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        resp = self._http.post(
            f"{self.url}{PIPELINE_PATH}",
            json={"requests": requests},
            headers=headers,
        )
        if resp.status_code >= 400:
            raise TursoError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        results = payload.get("results") or []
        # Surface the first error response, if any.
        for r in results:
            if r.get("type") == "error":
                err = r.get("error") or {}
                raise TursoError(
                    f"{err.get('code', 'TURSO_ERROR')}: {err.get('message', 'unknown')}"
                )
        return results

    def _execute_one(self, sql: str, params: Sequence[Any]) -> dict:
        if isinstance(params, dict):
            raise NotImplementedError("Turso adapter supports positional params only")
        args = [_type_for(p) for p in (params or ())]
        results = self._pipeline([
            {"type": "execute", "stmt": {"sql": sql, "args": args}},
        ])
        if not results:
            raise TursoError("empty pipeline response")
        first = results[0]
        return (first.get("response") or {}).get("result") or {}

    # ------------------------------------------------- sqlite3 shape

    def execute(self, sql: str, params: Sequence[Any] = ()) -> TursoCursor:
        cur = TursoCursor(self)
        cur.execute(sql, params)
        return cur

    def cursor(self) -> TursoCursor:
        return TursoCursor(self)

    _STATEMENT_SPLIT_RE = re.compile(r";\s*(?:\n|$)")

    def executescript(self, script: str) -> None:
        # Strip SQL comments, split on statement-terminating semicolons,
        # drop empty chunks. Good enough for our migrations.
        cleaned = re.sub(r"--[^\n]*", "", script)
        statements = [s.strip() for s in self._STATEMENT_SPLIT_RE.split(cleaned)]
        statements = [s for s in statements if s and not s.isspace()]
        requests = [
            {"type": "execute", "stmt": {"sql": s, "args": []}}
            for s in statements
        ]
        if not requests:
            return
        self._pipeline(requests)

    def commit(self) -> None:
        # Turso autocommits each pipeline execute; nothing to do.
        return

    def rollback(self) -> None:
        # No multi-statement transaction here to roll back.
        return

    def close(self) -> None:
        if self._owns_client:
            try:
                self._http.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
