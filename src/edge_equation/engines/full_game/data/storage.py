"""DuckDB-backed persistence layer for the full-game engine.

Mirrors `nrfi/data/storage.py` and `props_prizepicks/data/storage.py`
for shape consistency. Three tables — predictions, actuals, features.

`fullgame_predictions` — one row per (game_pk, market, side) we
projected on a given date. PK includes `event_date` so historical
backfills don't collide on re-runs::

    fullgame_predictions
        game_pk        BIGINT
        event_date     DATE
        market_type    VARCHAR    'ML' / 'Run_Line' / 'Total' / 'F5_Total' / 'F5_ML' / 'Team_Total'
        side           VARCHAR    'Over' / 'Under' / tricode
        team_tricode   VARCHAR    staked team for team-side markets, '' otherwise
        line_value     DOUBLE     spread/total number; NULL for ML
        model_prob     DOUBLE
        market_prob    DOUBLE     vig-adjusted implied
        edge_pp        DOUBLE
        american_odds  DOUBLE
        book           VARCHAR
        confidence     DOUBLE
        tier           VARCHAR
        feature_blob   VARCHAR    JSON audit dict
        created_at     TIMESTAMP
        PRIMARY KEY (event_date, market_type, side, line_value)

`fullgame_actuals` — realised outcome of the game::

    fullgame_actuals
        game_pk        BIGINT PRIMARY KEY
        event_date     DATE
        home_runs      INTEGER
        away_runs      INTEGER
        f5_home_runs   INTEGER
        f5_away_runs   INTEGER
        loaded_at      TIMESTAMP

`fullgame_features` — per-team rolling-rate snapshot used at projection
time. Wipe-and-rebuild safe — re-derivable from actuals.

::

    fullgame_features
        event_date     DATE
        team_tricode   VARCHAR
        feature_blob   VARCHAR    JSON {runs_per_game, runs_allowed_per_game, ...}
        loaded_at      TIMESTAMP
        PRIMARY KEY (event_date, team_tricode)
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS fullgame_predictions (
        game_pk        BIGINT,
        event_date     DATE,
        market_type    VARCHAR,
        side           VARCHAR,
        team_tricode   VARCHAR,
        line_value     DOUBLE,
        model_prob     DOUBLE,
        market_prob    DOUBLE,
        edge_pp        DOUBLE,
        american_odds  DOUBLE,
        book           VARCHAR,
        confidence     DOUBLE,
        tier           VARCHAR,
        feature_blob   VARCHAR,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (event_date, market_type, side, line_value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fullgame_actuals (
        game_pk        BIGINT PRIMARY KEY,
        event_date     DATE,
        home_team      VARCHAR,
        away_team      VARCHAR,
        home_runs      INTEGER,
        away_runs      INTEGER,
        f5_home_runs   INTEGER,
        f5_away_runs   INTEGER,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fullgame_features (
        event_date     DATE,
        team_tricode   VARCHAR,
        feature_blob   VARCHAR,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (event_date, team_tricode)
    )
    """,
)


# ALTER statements applied after _SCHEMA to upgrade existing DBs with
# columns added in later releases. ``CREATE TABLE IF NOT EXISTS`` won't
# add columns to an existing table; these ALTERs handle that case
# without forcing operators to drop and rebuild. Each statement is
# wrapped in a try/except inside ``_init_schema`` so a duplicate-column
# error on already-migrated DBs is a silent no-op.
_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE fullgame_actuals ADD COLUMN home_team VARCHAR",
    "ALTER TABLE fullgame_actuals ADD COLUMN away_team VARCHAR",
)


def _import_duckdb():
    try:
        import duckdb  # type: ignore
        return duckdb
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "duckdb is required for full_game.data.storage. "
            "Install via `pip install -e .[nrfi]`."
        ) from e


class FullGameStore:
    """Thin DuckDB wrapper. Mirrors `NRFIStore` / `PropsStore`."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._duckdb = _import_duckdb()
        self._conn = self._duckdb.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        for stmt in _SCHEMA:
            self._conn.execute(stmt)
        for stmt in _MIGRATIONS:
            # Migrations are idempotent ALTER statements; on a fresh DB the
            # CREATE TABLE above already includes the columns and the ALTER
            # is a no-op or harmless duplicate-column error we swallow.
            try:
                self._conn.execute(stmt)
            except Exception:
                pass

    @contextlib.contextmanager
    def cursor(self) -> Iterator[Any]:
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def upsert(self, table: str, rows: Iterable[dict]) -> int:
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
        # subsequent subprocess connections see the data. Without
        # this, the backfill subprocess writes 821 rows, exits, and
        # the next process opens the file but reads ``COUNT(*) = 0``
        # — DuckDB's WAL contained the writes but the connection
        # close didn't promote them. Idempotent + cheap.
        try:
            self._conn.execute("CHECKPOINT")
        except Exception:
            pass
        return len(rows)

    def query_df(self, sql: str, params: Optional[tuple] = None):
        return self._conn.execute(sql, params or ()).fetchdf()

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        self._conn.execute(sql, params or ())

    def close(self) -> None:
        # Force a final checkpoint before close so any WAL contents
        # not yet promoted (e.g. from non-upsert ``execute()`` calls)
        # land in the main file. Belt-and-suspenders alongside the
        # per-upsert checkpoint above.
        try:
            self._conn.execute("CHECKPOINT")
        except Exception:
            pass
        self._conn.close()

    # --- Convenience accessors --------------------------------------------

    def predictions_for_date(self, event_date: str):
        return self.query_df(
            """
            SELECT * FROM fullgame_predictions
            WHERE event_date = ?
            ORDER BY edge_pp DESC
            """,
            (event_date,),
        )
