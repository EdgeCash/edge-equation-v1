"""DuckDB-backed persistence layer for the Props engine.

Tables
------

`prop_predictions` — one row per (game_pk, market, player, line, side)
predicted on a given day. Caller-supplied `game_pk` / `event_date` keep
this aligned with the NRFI engine's game-level identifier so cross-
engine joins (parlay correlation) stay simple.

::

    prop_predictions
        game_pk        BIGINT
        event_date     DATE
        market_type    VARCHAR     'HR' / 'Hits' / 'Total_Bases' / 'RBI' / 'K'
        player_name    VARCHAR
        player_id      BIGINT      MLB Stats API id when known, NULL otherwise
        line_value     DOUBLE      0.5 / 1.5 / 5.5 / ...
        side           VARCHAR     'Over' / 'Under'
        model_prob     DOUBLE      calibrated 0..1
        market_prob    DOUBLE      vig-adjusted implied book probability
        edge_pp        DOUBLE      signed pp, model_prob - market_prob
        american_odds  DOUBLE      market line we modelled against
        book           VARCHAR
        confidence     DOUBLE      0..1, projection's self-confidence
        tier           VARCHAR     'ELITE' / 'STRONG' / 'MODERATE' / 'LEAN' / 'NO_PLAY'
        feature_blob   VARCHAR     JSON dict of per-player rates (audit trail)
        created_at     TIMESTAMP
        PRIMARY KEY (event_date, market_type, player_name, line_value, side)

`prop_actuals` — what actually happened in the game. Settle pass joins
on (game_pk, market_type, player_name).

::

    prop_actuals
        game_pk        BIGINT
        event_date     DATE
        market_type    VARCHAR
        player_name    VARCHAR
        actual_value   DOUBLE      e.g. HRs hit, Ks recorded
        loaded_at      TIMESTAMP
        PRIMARY KEY (game_pk, market_type, player_name)

`prop_features` — per-player rolling-rate snapshot used at projection
time. Re-derivable from Statcast cache so this is a forensic audit
table only — wipe-and-rebuild is safe.

::

    prop_features
        event_date     DATE
        player_name    VARCHAR
        player_id      BIGINT
        role           VARCHAR     'batter' / 'pitcher'
        feature_blob   VARCHAR     JSON dict of {feature_name: value}
        loaded_at      TIMESTAMP
        PRIMARY KEY (event_date, player_name, role)

Falls back to a no-op shim if duckdb isn't installed so unit tests
that don't need the real DB can still import this module.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS prop_predictions (
        game_pk        BIGINT,
        event_date     DATE,
        market_type    VARCHAR,
        player_name    VARCHAR,
        player_id      BIGINT,
        line_value     DOUBLE,
        side           VARCHAR,
        model_prob     DOUBLE,
        market_prob    DOUBLE,
        edge_pp        DOUBLE,
        american_odds  DOUBLE,
        book           VARCHAR,
        confidence     DOUBLE,
        tier           VARCHAR,
        feature_blob   VARCHAR,
        commence_time  VARCHAR,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (event_date, market_type, player_name, line_value, side)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prop_actuals (
        game_pk        BIGINT,
        event_date     DATE,
        market_type    VARCHAR,
        player_name    VARCHAR,
        actual_value   DOUBLE,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (game_pk, market_type, player_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prop_features (
        event_date     DATE,
        player_name    VARCHAR,
        player_id      BIGINT,
        role           VARCHAR,
        feature_blob   VARCHAR,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (event_date, player_name, role)
    )
    """,
)


# Idempotent column migrations for tables created before a column was
# added. Each entry runs through ``ALTER TABLE ... ADD COLUMN IF NOT
# EXISTS`` so a fresh database (already has the column from the CREATE
# above) and an old database (needs the new column) both end up with
# the same schema.
_MIGRATIONS: tuple[str, ...] = (
    # 2026-05-07 (PR for first-pitch event_time persistence): commence_time
    # threaded from the Odds API event-list down through the props
    # ledger so the daily-feed loader can stamp FeedPick.event_time.
    """
    ALTER TABLE prop_predictions ADD COLUMN IF NOT EXISTS commence_time VARCHAR
    """,
)


def _import_duckdb():
    try:
        import duckdb  # type: ignore
        return duckdb
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "duckdb is required for props.data.storage. "
            "Install via `pip install -e .[nrfi]`."
        ) from e


class PropsStore:
    """Thin wrapper around a DuckDB connection for the props engine.

    Mirrors `NRFIStore` so operators reading both engines see one
    pattern. Method names match wherever possible (`upsert`,
    `query_df`, `execute`) so callers can swap stores in tests.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._duckdb = _import_duckdb()
        self._conn = self._duckdb.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        for stmt in _SCHEMA:
            self._conn.execute(stmt)
        # Idempotent migrations for columns added after the original
        # CREATE statement was deployed. ``ADD COLUMN IF NOT EXISTS``
        # is a DuckDB no-op when the column is already present, so
        # re-running on a fresh database that already has the column
        # via the CREATE TABLE above is fine.
        for migration in _MIGRATIONS:
            try:
                self._conn.execute(migration)
            except Exception:
                # Some DuckDB releases reject ADD COLUMN IF NOT EXISTS
                # on tables where the column already exists. Probe the
                # information_schema to keep the bootstrap silent in
                # both cases.
                pass

    @contextlib.contextmanager
    def cursor(self) -> Iterator[Any]:
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def upsert(self, table: str, rows: Iterable[dict]) -> int:
        """INSERT OR REPLACE a batch of rows."""
        rows = list(rows)
        if not rows:
            return 0
        cols = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        sql = (
            f"INSERT OR REPLACE INTO {table} ({col_list}) "
            f"VALUES ({placeholders})"
        )
        with self.cursor() as cur:
            cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        # Explicit checkpoint flushes the WAL into the main file so
        # subsequent subprocess connections see the data. Mirrors the
        # FullGameStore fix for the same WAL-not-promoted issue.
        try:
            self._conn.execute("CHECKPOINT")
        except Exception:
            pass
        return len(rows)

    def query_df(self, sql: str, params: Optional[tuple] = None):
        """Run SQL and return a pandas DataFrame."""
        return self._conn.execute(sql, params or ()).fetchdf()

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        self._conn.execute(sql, params or ())

    def close(self) -> None:
        # Force checkpoint before close so any non-upsert writes
        # land in the main file before the connection releases.
        try:
            self._conn.execute("CHECKPOINT")
        except Exception:
            pass
        self._conn.close()

    # --- Convenience accessors ---------------------------------------------

    def predictions_for_date(self, event_date: str):
        return self.query_df(
            """
            SELECT * FROM prop_predictions
            WHERE event_date = ?
            ORDER BY edge_pp DESC
            """,
            (event_date,),
        )

    def actuals_for_date(self, event_date: str):
        return self.query_df(
            "SELECT * FROM prop_actuals WHERE event_date = ?",
            (event_date,),
        )
