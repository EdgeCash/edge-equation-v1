import os
import sqlite3
import pytest

from edge_equation.persistence.db import (
    Database,
    DEFAULT_DB_ENV_VAR,
    DEFAULT_DB_PATH,
    MIGRATIONS,
)


@pytest.fixture
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


def test_resolve_path_override_wins():
    assert Database.resolve_path("/tmp/custom.db") == "/tmp/custom.db"


def test_resolve_path_env_var_wins(monkeypatch):
    monkeypatch.setenv(DEFAULT_DB_ENV_VAR, "/tmp/env.db")
    assert Database.resolve_path(None) == "/tmp/env.db"


def test_resolve_path_default(monkeypatch):
    monkeypatch.delenv(DEFAULT_DB_ENV_VAR, raising=False)
    assert Database.resolve_path(None) == DEFAULT_DB_PATH


def test_current_version_empty_db(mem_conn):
    assert Database.current_version(mem_conn) == 0


def test_migrate_applies_all_migrations(mem_conn):
    n = Database.migrate(mem_conn)
    assert n == len(MIGRATIONS)
    assert Database.current_version(mem_conn) == MIGRATIONS[-1][0]


def test_migrate_idempotent(mem_conn):
    Database.migrate(mem_conn)
    n = Database.migrate(mem_conn)
    assert n == 0


def test_migrate_creates_all_tables(mem_conn):
    Database.migrate(mem_conn)
    rows = mem_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "schema_migrations" in names
    assert "slates" in names
    assert "picks" in names
    assert "odds_cache" in names
    assert "realizations" in names


def test_migrate_creates_indexes(mem_conn):
    Database.migrate(mem_conn)
    rows = mem_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "idx_picks_game_id" in names
    assert "idx_picks_slate_id" in names
    assert "idx_odds_cache_expires_at" in names


def test_open_with_in_memory_path():
    conn = Database.open(":memory:")
    try:
        Database.migrate(conn)
        assert Database.current_version(conn) > 0
    finally:
        conn.close()


def test_open_file_db_creates_file(tmp_path):
    path = str(tmp_path / "test.db")
    conn = Database.open(path)
    try:
        Database.migrate(conn)
    finally:
        conn.close()
    assert os.path.exists(path)


def test_realizations_unique_constraint(mem_conn):
    Database.migrate(mem_conn)
    mem_conn.execute(
        "INSERT INTO realizations (game_id, market_type, selection, outcome, recorded_at) VALUES (?, ?, ?, ?, ?)",
        ("G1", "ML", "BOS", "win", "2026-04-20T12:00:00"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        mem_conn.execute(
            "INSERT INTO realizations (game_id, market_type, selection, outcome, recorded_at) VALUES (?, ?, ?, ?, ?)",
            ("G1", "ML", "BOS", "loss", "2026-04-20T13:00:00"),
        )


def test_foreign_keys_enabled(mem_conn):
    Database.migrate(mem_conn)
    row = mem_conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1
