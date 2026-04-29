"""DuckDB-backed persistence layer for the NRFI engine.

Tables
------
games          : one row per scheduled MLB game
pitchers       : pitcher reference (probables, hand, IDs)
batters        : batter reference + season stats snapshot
umpires        : home-plate umpire reference + 2026 ABS metrics
weather        : per-game weather snapshot (forecast or actual)
features       : engineered feature row per game per model version
predictions    : model output (NRFI%, λ, color, MC band, SHAP top-N)
actuals        : realized first-inning outcome (NRFI 0/1, runs)

DuckDB is preferred over SQLite for the analytical workload (columnar
storage, native parquet IO, vectorised aggregations). Falls back to a
no-op shim if duckdb isn't installed so unit tests can still import.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS games (
        game_pk        BIGINT PRIMARY KEY,
        game_date      DATE NOT NULL,
        season         INTEGER NOT NULL,
        home_team      VARCHAR NOT NULL,
        away_team      VARCHAR NOT NULL,
        venue_code     VARCHAR NOT NULL,
        venue_name     VARCHAR,
        first_pitch_ts TIMESTAMP,
        roof_status    VARCHAR,
        home_pitcher_id BIGINT,
        away_pitcher_id BIGINT,
        home_pitcher_hand VARCHAR,
        away_pitcher_hand VARCHAR,
        home_lineup    VARCHAR, -- comma-separated batter IDs
        away_lineup    VARCHAR,
        ump_id         BIGINT,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pitchers (
        pitcher_id     BIGINT PRIMARY KEY,
        full_name      VARCHAR,
        throws         VARCHAR,
        team           VARCHAR,
        last_updated   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batters (
        batter_id      BIGINT PRIMARY KEY,
        full_name      VARCHAR,
        bats           VARCHAR,
        team           VARCHAR,
        last_updated   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS umpires (
        ump_id         BIGINT PRIMARY KEY,
        full_name      VARCHAR,
        zone_size_idx      DOUBLE,  -- 100 = average; >100 = wider zone
        run_environment_idx DOUBLE,
        called_strike_above DOUBLE, -- league-relative called-strike %
        abs_overturn_rate   DOUBLE, -- 2026 ABS challenge overturn rate vs ump
        last_updated   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weather (
        game_pk        BIGINT PRIMARY KEY,
        source         VARCHAR,  -- 'forecast' or 'archive'
        as_of_ts       TIMESTAMP,
        temperature_f  DOUBLE,
        wind_speed_mph DOUBLE,
        wind_dir_deg   DOUBLE,
        humidity_pct   DOUBLE,
        dew_point_f    DOUBLE,
        air_density    DOUBLE,
        precip_prob    DOUBLE,
        roof_open      BOOLEAN
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS features (
        game_pk        BIGINT,
        model_version  VARCHAR,
        feature_blob   VARCHAR,  -- JSON-serialized feature dict
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (game_pk, model_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS predictions (
        game_pk        BIGINT,
        model_version  VARCHAR,
        nrfi_prob      DOUBLE,    -- calibrated 0-1
        nrfi_pct       DOUBLE,    -- 0-100
        lambda_total   DOUBLE,    -- expected first-inning runs
        color_band     VARCHAR,
        color_hex      VARCHAR,
        signal         VARCHAR,
        poisson_p_nrfi DOUBLE,
        ml_p_nrfi      DOUBLE,
        blended_p_nrfi DOUBLE,
        mc_low         DOUBLE,
        mc_high        DOUBLE,
        mc_band_pp     DOUBLE,
        shap_drivers   VARCHAR,   -- JSON list of (feature, contribution)
        driver_text    VARCHAR,   -- JSON list of display-ready drivers
        market_prob    DOUBLE,
        edge           DOUBLE,
        edge_pp        DOUBLE,
        kelly_units    DOUBLE,
        kelly_suggestion VARCHAR,
        tier           VARCHAR,
        tier_basis     VARCHAR,
        tier_value     DOUBLE,
        tier_band      VARCHAR,
        probability_display VARCHAR,
        sort_edge      DOUBLE,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (game_pk, model_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS actuals (
        game_pk        BIGINT PRIMARY KEY,
        first_inn_runs INTEGER,
        nrfi           BOOLEAN,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
)


def _import_duckdb():
    try:
        import duckdb  # type: ignore
        return duckdb
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "duckdb is required for nrfi.data.storage. "
            "Install via `pip install -r nrfi/requirements-nrfi.txt`."
        ) from e


class NRFIStore:
    """Thin wrapper around a DuckDB connection."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._duckdb = _import_duckdb()
        self._conn = self._duckdb.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        for stmt in _SCHEMA:
            self._conn.execute(stmt)
        self._migrate_prediction_columns()

    def _migrate_prediction_columns(self) -> None:
        """Additive migrations for existing NRFI DuckDB files.

        DuckDB's ``CREATE TABLE IF NOT EXISTS`` will not update an existing
        table, so enriched production output must register new nullable columns
        explicitly.  Every statement is idempotent to keep local/CI runs safe.
        """
        for col, typ in (
            ("color_hex", "VARCHAR"),
            ("poisson_p_nrfi", "DOUBLE"),
            ("ml_p_nrfi", "DOUBLE"),
            ("blended_p_nrfi", "DOUBLE"),
            ("mc_band_pp", "DOUBLE"),
            ("driver_text", "VARCHAR"),
            ("edge_pp", "DOUBLE"),
            ("kelly_suggestion", "VARCHAR"),
            ("tier", "VARCHAR"),
            ("tier_basis", "VARCHAR"),
            ("tier_value", "DOUBLE"),
            ("tier_band", "VARCHAR"),
            ("probability_display", "VARCHAR"),
            ("sort_edge", "DOUBLE"),
            ("conviction_color", "VARCHAR"),
            ("conviction_hex", "VARCHAR"),
            ("conviction_rank", "INTEGER"),
        ):
            self._conn.execute(
                f"ALTER TABLE predictions ADD COLUMN IF NOT EXISTS {col} {typ}"
            )

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
        sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
        with self.cursor() as cur:
            cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        return len(rows)

    def query_df(self, sql: str, params: Optional[tuple] = None):
        """Run SQL and return a pandas DataFrame."""
        return self._conn.execute(sql, params or ()).fetchdf()

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        self._conn.execute(sql, params or ())

    def close(self) -> None:
        self._conn.close()

    # --- Convenience accessors ---------------------------------------------
    def games_for_date(self, game_date: str):
        return self.query_df(
            "SELECT * FROM games WHERE game_date = ? ORDER BY first_pitch_ts",
            (game_date,),
        )

    def predictions_for_date(self, game_date: str):
        return self.query_df(
            """
            SELECT p.*, g.home_team, g.away_team, g.venue_code, g.first_pitch_ts
            FROM predictions p JOIN games g USING(game_pk)
            WHERE g.game_date = ? ORDER BY g.first_pitch_ts
            """,
            (game_date,),
        )

    def training_frame(self, start_date: str, end_date: str):
        """Join features × actuals for model training."""
        return self.query_df(
            """
            SELECT f.game_pk, f.model_version, f.feature_blob,
                   a.first_inn_runs, a.nrfi
            FROM features f JOIN actuals a USING(game_pk)
            JOIN games g USING(game_pk)
            WHERE g.game_date BETWEEN ? AND ?
            """,
            (start_date, end_date),
        )
