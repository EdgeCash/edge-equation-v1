"""DuckDB persistence layer for the football engines.

Single DuckDB per sport (`data/nfl_cache/nfl.duckdb`,
`data/ncaaf_cache/ncaaf.duckdb`) so the operator can blow one away
without touching the other. The schema is identical between sports —
only the data flowing in differs.

Tables
~~~~~~

`football_games` — one row per scheduled game::

    game_id        VARCHAR PK     'nfl_2025_01_KC_BAL' or cfbd id
    sport          VARCHAR        'NFL' or 'NCAAF'
    season         INTEGER
    week           INTEGER        1-18 (NFL) or 1-15 (NCAAF regular)
    season_type    VARCHAR        'REG' / 'POST' / 'BOWL'
    event_date     DATE
    kickoff_ts     TIMESTAMP      first-kick UTC
    home_team      VARCHAR
    away_team      VARCHAR
    home_tricode   VARCHAR
    away_tricode   VARCHAR
    venue          VARCHAR
    venue_code     VARCHAR        canonical short code
    is_dome        BOOLEAN
    is_neutral_site BOOLEAN
    loaded_at      TIMESTAMP

`football_actuals` — final scores + box-derived stats::

    game_id        VARCHAR PK
    home_score     INTEGER
    away_score     INTEGER
    home_yards     INTEGER
    away_yards     INTEGER
    home_turnovers INTEGER
    away_turnovers INTEGER
    overtime       BOOLEAN
    final_status   VARCHAR        'FINAL' / 'CANCELLED' / 'POSTPONED'
    loaded_at      TIMESTAMP

`football_plays` — per-play log (nflverse / cfbfastR)::

    game_id        VARCHAR
    play_id        VARCHAR
    sport          VARCHAR
    quarter        INTEGER
    seconds_remaining INTEGER
    down           INTEGER
    yards_to_go    INTEGER
    yardline       INTEGER
    play_type      VARCHAR        'pass' / 'run' / 'kick' / 'special'
    epa            DOUBLE
    success        BOOLEAN
    home_wp        DOUBLE         home win probability before snap
    rusher_id      VARCHAR
    passer_id      VARCHAR
    receiver_id    VARCHAR
    PRIMARY KEY (game_id, play_id)

`football_props` — historical player-prop lines + actuals::

    game_id        VARCHAR
    player_id      VARCHAR
    player_name    VARCHAR
    market         VARCHAR        'Pass_Yds' / 'Rush_Yds' / 'Anytime_TD' / ...
    side           VARCHAR        'Over' / 'Under' / 'Yes' / 'No'
    line_value     DOUBLE
    american_odds  DOUBLE
    book           VARCHAR
    line_captured_at TIMESTAMP
    actual_value   DOUBLE         backfilled after game
    actual_hit     BOOLEAN
    PRIMARY KEY (game_id, player_id, market, side, line_value)

`football_lines` — historical game-level Spread / Total / ML::

    game_id        VARCHAR
    market         VARCHAR        'Spread' / 'Total' / 'ML'
    side           VARCHAR        'home' / 'away' / 'over' / 'under'
    line_value     DOUBLE         spread / total; 0.0 for ML
    american_odds  DOUBLE
    book           VARCHAR
    line_captured_at TIMESTAMP    when this snapshot was taken
    is_closing     BOOLEAN        TRUE for the closest-to-kickoff snapshot
    PRIMARY KEY (game_id, market, side, line_value, book, line_captured_at)

`football_weather` — per-game weather snapshot::

    game_id        VARCHAR PK
    sport          VARCHAR
    source         VARCHAR        'open-meteo-archive' / 'forecast'
    captured_at    TIMESTAMP
    temperature_f  DOUBLE
    wind_speed_mph DOUBLE
    wind_dir_deg   DOUBLE
    humidity_pct   DOUBLE
    precipitation_prob DOUBLE
    is_indoor      BOOLEAN

`football_features` — per-team rolling feature snapshot at game time::

    game_id        VARCHAR
    team_tricode   VARCHAR
    sport          VARCHAR
    feature_blob   VARCHAR        JSON dict of team-level rates
    loaded_at      TIMESTAMP
    PRIMARY KEY (game_id, team_tricode)

`football_backfill_checkpoints` — resumability tracking. PK is
(sport, target_date, op) so re-running the backfill is a no-op for
already-completed (date, op) pairs::

    sport          VARCHAR
    target_date    DATE
    op             VARCHAR        'games' / 'plays' / 'odds' / 'weather' / 'actuals'
    completed_at   TIMESTAMP
    rows_loaded    INTEGER
    error          VARCHAR        non-null when the run failed
    PRIMARY KEY (sport, target_date, op)
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS football_games (
        game_id        VARCHAR,
        sport          VARCHAR,
        season         INTEGER,
        week           INTEGER,
        season_type    VARCHAR,
        event_date     DATE,
        kickoff_ts     TIMESTAMP,
        home_team      VARCHAR,
        away_team      VARCHAR,
        home_tricode   VARCHAR,
        away_tricode   VARCHAR,
        venue          VARCHAR,
        venue_code     VARCHAR,
        is_dome        BOOLEAN,
        is_neutral_site BOOLEAN,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (game_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS football_actuals (
        game_id        VARCHAR PRIMARY KEY,
        home_score     INTEGER,
        away_score     INTEGER,
        home_yards     INTEGER,
        away_yards     INTEGER,
        home_turnovers INTEGER,
        away_turnovers INTEGER,
        overtime       BOOLEAN,
        final_status   VARCHAR,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS football_plays (
        game_id        VARCHAR,
        play_id        VARCHAR,
        sport          VARCHAR,
        quarter        INTEGER,
        seconds_remaining INTEGER,
        down           INTEGER,
        yards_to_go    INTEGER,
        yardline       INTEGER,
        play_type      VARCHAR,
        epa            DOUBLE,
        success        BOOLEAN,
        home_wp        DOUBLE,
        rusher_id      VARCHAR,
        passer_id      VARCHAR,
        receiver_id    VARCHAR,
        PRIMARY KEY (game_id, play_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS football_props (
        game_id        VARCHAR,
        player_id      VARCHAR,
        player_name    VARCHAR,
        market         VARCHAR,
        side           VARCHAR,
        line_value     DOUBLE,
        american_odds  DOUBLE,
        book           VARCHAR,
        line_captured_at TIMESTAMP,
        actual_value   DOUBLE,
        actual_hit     BOOLEAN,
        PRIMARY KEY (game_id, player_id, market, side, line_value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS football_lines (
        game_id        VARCHAR,
        market         VARCHAR,
        side           VARCHAR,
        line_value     DOUBLE,
        american_odds  DOUBLE,
        book           VARCHAR,
        line_captured_at TIMESTAMP,
        is_closing     BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (game_id, market, side, line_value, book, line_captured_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS football_weather (
        game_id        VARCHAR PRIMARY KEY,
        sport          VARCHAR,
        source         VARCHAR,
        captured_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        temperature_f  DOUBLE,
        wind_speed_mph DOUBLE,
        wind_dir_deg   DOUBLE,
        humidity_pct   DOUBLE,
        precipitation_prob DOUBLE,
        is_indoor      BOOLEAN
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS football_features (
        game_id        VARCHAR,
        team_tricode   VARCHAR,
        sport          VARCHAR,
        feature_blob   VARCHAR,
        loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (game_id, team_tricode)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS football_backfill_checkpoints (
        sport          VARCHAR,
        target_date    DATE,
        op             VARCHAR,
        completed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        rows_loaded    INTEGER,
        error          VARCHAR,
        PRIMARY KEY (sport, target_date, op)
    )
    """,
)


def _import_duckdb():
    try:
        import duckdb  # type: ignore
        return duckdb
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "duckdb is required for football_core.data.storage. "
            "Install via `pip install -e .[nrfi]` (the nrfi extras "
            "carry duckdb)."
        ) from e


class FootballStore:
    """Thin DuckDB wrapper. Mirrors `NRFIStore` / `FullGameStore` so
    operators / contributors crossing engines see one shape.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._duckdb = _import_duckdb()
        self._conn = self._duckdb.connect(self.db_path)
        init_schema(self)

    @contextlib.contextmanager
    def cursor(self) -> Iterator[Any]:
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        self._conn.execute(sql, params or ())

    def query_df(self, sql: str, params: Optional[tuple] = None):
        return self._conn.execute(sql, params or ()).fetchdf()

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
        return len(rows)

    def close(self) -> None:
        self._conn.close()


def init_schema(store) -> None:
    """Idempotent — safe to call on every store open. Mirrors the
    pattern NRFIStore / FullGameStore use."""
    for stmt in _SCHEMA:
        store.execute(stmt)
