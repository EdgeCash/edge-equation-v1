"""
Database connection and deterministic schema migrations.

Database:
- open(path): returns a sqlite3.Connection configured with foreign keys on,
  row_factory=sqlite3.Row, and isolation_level=None (autocommit; use explicit
  transactions via connect-as-context-manager or BEGIN/COMMIT).
- migrate(conn): runs every pending migration in order; idempotent.
- current_version(conn): highest applied schema version.

Migrations are pure SQL strings held in MIGRATIONS, indexed by version.
Adding a new migration = append to the tuple with the next version number.
Never edit a shipped migration -- append only.
"""
import os
import sqlite3
from typing import Optional, Tuple


DEFAULT_DB_ENV_VAR = "EDGE_EQUATION_DB"
DEFAULT_DB_PATH = "edge_equation.db"


MIGRATIONS: Tuple[Tuple[int, str], ...] = (
    (1, """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS slates (
            slate_id TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            sport TEXT,
            card_type TEXT,
            metadata_json TEXT
        );

        CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slate_id TEXT,
            game_id TEXT,
            sport TEXT NOT NULL,
            market_type TEXT NOT NULL,
            selection TEXT NOT NULL,
            odds INTEGER,
            line_number TEXT,
            fair_prob TEXT,
            expected_value TEXT,
            edge TEXT,
            kelly TEXT,
            grade TEXT,
            realization INTEGER,
            decay_halflife_days TEXT,
            hfa_value TEXT,
            kelly_breakdown_json TEXT,
            event_time TEXT,
            metadata_json TEXT,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (slate_id) REFERENCES slates(slate_id)
        );

        CREATE INDEX IF NOT EXISTS idx_picks_game_id ON picks(game_id);
        CREATE INDEX IF NOT EXISTS idx_picks_slate_id ON picks(slate_id);
        CREATE INDEX IF NOT EXISTS idx_picks_sport ON picks(sport);
        CREATE INDEX IF NOT EXISTS idx_picks_recorded_at ON picks(recorded_at);

        CREATE TABLE IF NOT EXISTS odds_cache (
            cache_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_odds_cache_expires_at ON odds_cache(expires_at);

        CREATE TABLE IF NOT EXISTS realizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            market_type TEXT NOT NULL,
            selection TEXT NOT NULL,
            outcome TEXT NOT NULL,
            actual_value TEXT,
            recorded_at TEXT NOT NULL,
            UNIQUE(game_id, market_type, selection)
        );

        CREATE INDEX IF NOT EXISTS idx_realizations_game_id ON realizations(game_id);
        CREATE INDEX IF NOT EXISTS idx_realizations_recorded_at ON realizations(recorded_at);
    """),
    (2, """
        CREATE TABLE IF NOT EXISTS game_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            league TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            start_time TEXT NOT NULL,
            home_score INTEGER NOT NULL,
            away_score INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'final',
            recorded_at TEXT NOT NULL,
            UNIQUE(game_id)
        );

        CREATE INDEX IF NOT EXISTS idx_game_results_league ON game_results(league);
        CREATE INDEX IF NOT EXISTS idx_game_results_start_time ON game_results(start_time);
        CREATE INDEX IF NOT EXISTS idx_game_results_home_team ON game_results(home_team);
        CREATE INDEX IF NOT EXISTS idx_game_results_away_team ON game_results(away_team);
    """),
)


class Database:
    """
    Connection factory plus migration runner:
    - open(path)            -> sqlite3.Connection
    - resolve_path(override) -> str (env var or default if override is None)
    - migrate(conn)         -> number of migrations applied
    - current_version(conn) -> int (0 if no schema_migrations table)
    """

    @staticmethod
    def resolve_path(override: Optional[str] = None) -> str:
        if override is not None:
            return override
        return os.environ.get(DEFAULT_DB_ENV_VAR, DEFAULT_DB_PATH)

    @staticmethod
    def open(path: Optional[str] = None) -> sqlite3.Connection:
        resolved = Database.resolve_path(path)
        conn = sqlite3.connect(resolved)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def current_version(conn: sqlite3.Connection) -> int:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if cur.fetchone() is None:
            return 0
        row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
        return int(row["v"])

    @staticmethod
    def migrate(conn: sqlite3.Connection) -> int:
        current = Database.current_version(conn)
        applied = 0
        for version, sql in MIGRATIONS:
            if version <= current:
                continue
            # executescript commits any pending transaction and runs the DDL
            # as its own transaction. Then we record the applied version.
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                (version,),
            )
            conn.commit()
            applied += 1
        return applied
