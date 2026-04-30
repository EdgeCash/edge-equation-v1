"""Tests for the football data layer (storage + checkpoints + loaders +
backfill orchestrators).

duckdb isn't installed in the lighter test env, so the storage tests
verify the schema strings; the orchestrator tests use a fake store +
injected loaders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake store — minimal subset of FootballStore the orchestrators touch
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self):
        self.upserts: list[tuple[str, list[dict]]] = []
        self.checkpoints: list[dict] = []
        self.tables: dict[str, list[dict]] = {}

    def execute(self, sql, params=None):
        return None

    def upsert(self, table: str, rows):
        rows = list(rows)
        self.upserts.append((table, rows))
        if table == "football_backfill_checkpoints":
            for r in rows:
                self.checkpoints = [
                    c for c in self.checkpoints
                    if not (c["sport"] == r["sport"]
                            and c["target_date"] == r["target_date"]
                            and c["op"] == r["op"])
                ]
                self.checkpoints.append(dict(r))
        else:
            self.tables.setdefault(table, []).extend(rows)
        return len(rows)

    def query_df(self, sql, params=None):
        if "football_backfill_checkpoints" in sql:
            sport = params[0] if params else None
            rows = [c for c in self.checkpoints
                    if c["sport"] == sport and c.get("error") is None]
            return _FakeDF(rows)
        if "football_games" in sql:
            sport = params[0] if params else None
            season = params[1] if params and len(params) > 1 else None
            rows = []
            for r in self.tables.get("football_games", []):
                if sport and r.get("sport") != sport:
                    continue
                if season is not None and r.get("season") != season:
                    continue
                rows.append(r)
            return _FakeDF(rows)
        return _FakeDF([])


class _FakeDF:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.empty = len(rows) == 0
        if rows:
            for col in rows[0].keys():
                setattr(self, col, [r.get(col) for r in rows])

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        for i, r in enumerate(self._rows):
            yield i, _Row(r)

    def to_dict(self, orient="records"):
        return list(self._rows)


class _Row:
    def __init__(self, d):
        self._d = d
        for k, v in d.items():
            setattr(self, k, v)

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __getitem__(self, key):
        return self._d[key]


# ---------------------------------------------------------------------------
# Schema / DDL tests (storage.py)
# ---------------------------------------------------------------------------


def test_schema_has_eight_tables():
    from edge_equation.engines.football_core.data.storage import _SCHEMA
    blob = " ".join(_SCHEMA)
    for table in (
        "football_games", "football_actuals", "football_plays",
        "football_props", "football_lines", "football_weather",
        "football_features", "football_backfill_checkpoints",
    ):
        assert table in blob, f"missing table {table}"


def test_schema_games_pk_is_game_id():
    from edge_equation.engines.football_core.data.storage import _SCHEMA
    games_ddl = next(s for s in _SCHEMA if "football_games" in s)
    assert "PRIMARY KEY (game_id)" in games_ddl


def test_schema_plays_pk_includes_play_id():
    from edge_equation.engines.football_core.data.storage import _SCHEMA
    plays_ddl = next(s for s in _SCHEMA if "football_plays" in s)
    assert "PRIMARY KEY (game_id, play_id)" in plays_ddl


def test_schema_checkpoints_pk_includes_sport():
    """Sport is part of the checkpoint PK so NFL + NCAAF can share
    one DuckDB without colliding on the same date+op."""
    from edge_equation.engines.football_core.data.storage import _SCHEMA
    cp_ddl = next(s for s in _SCHEMA
                    if "football_backfill_checkpoints" in s)
    assert "PRIMARY KEY (sport, target_date, op)" in cp_ddl


def test_schema_lines_pk_includes_book_and_capture_time():
    """Multiple book quotes per (game, market, side) at different
    times must coexist."""
    from edge_equation.engines.football_core.data.storage import _SCHEMA
    lines_ddl = next(s for s in _SCHEMA if "football_lines" in s)
    assert "book" in lines_ddl
    assert "line_captured_at" in lines_ddl


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


def test_chunk_dates_splits_into_weekly_chunks():
    from edge_equation.engines.football_core.data.checkpoints import (
        chunk_dates,
    )
    chunks = chunk_dates("2025-09-01", "2025-09-21", chunk_days=7)
    assert chunks == [
        ("2025-09-01", "2025-09-07"),
        ("2025-09-08", "2025-09-14"),
        ("2025-09-15", "2025-09-21"),
    ]


def test_chunk_dates_handles_partial_last_chunk():
    from edge_equation.engines.football_core.data.checkpoints import (
        chunk_dates,
    )
    chunks = chunk_dates("2025-09-01", "2025-09-10", chunk_days=7)
    assert chunks[-1] == ("2025-09-08", "2025-09-10")


def test_chunk_dates_returns_empty_when_end_before_start():
    from edge_equation.engines.football_core.data.checkpoints import (
        chunk_dates,
    )
    assert chunk_dates("2025-09-10", "2025-09-01") == []


def test_record_completion_writes_checkpoint_row():
    from edge_equation.engines.football_core.data.checkpoints import (
        record_completion, completed_pairs,
    )
    store = _FakeStore()
    record_completion(
        store, sport="NFL", target_date="2025-01-01",
        op="games", rows_loaded=272,
    )
    assert ("2025-01-01", "games") in completed_pairs(store, sport="NFL")


def test_record_failure_excluded_from_completed_pairs():
    from edge_equation.engines.football_core.data.checkpoints import (
        record_failure, completed_pairs,
    )
    store = _FakeStore()
    record_failure(
        store, sport="NFL", target_date="2025-01-01",
        op="games", error="HTTP 500",
    )
    assert completed_pairs(store, sport="NFL") == set()


def test_record_failure_truncates_long_error_messages():
    from edge_equation.engines.football_core.data.checkpoints import (
        record_failure,
    )
    store = _FakeStore()
    long_err = "x" * 1000
    record_failure(
        store, sport="NFL", target_date="2025-01-01",
        op="games", error=long_err,
    )
    assert len(store.checkpoints[0]["error"]) == 500


def test_completed_pairs_scopes_by_sport():
    """NFL checkpoint shouldn't show up when querying NCAAF."""
    from edge_equation.engines.football_core.data.checkpoints import (
        record_completion, completed_pairs,
    )
    store = _FakeStore()
    record_completion(
        store, sport="NFL", target_date="2025-01-01", op="games",
    )
    assert completed_pairs(store, sport="NCAAF") == set()


# ---------------------------------------------------------------------------
# Loader normalizer tests (no network)
# ---------------------------------------------------------------------------


def test_nflverse_games_normalizer_maps_columns():
    pd = pytest.importorskip("pandas")
    from edge_equation.engines.football_core.data.nflverse_loader import (
        _normalize_games_df,
    )
    raw = pd.DataFrame([{
        "game_id": "2025_01_KC_BAL", "week": 1, "season_type": "REG",
        "gameday": "2025-09-04", "gametime": "20:20",
        "home_team": "BAL", "away_team": "KC",
        "stadium": "M&T Bank Stadium", "stadium_id": "BAL00",
        "roof": "outdoors", "location": "Home",
    }])
    out = _normalize_games_df(raw, season=2025)
    row = out.iloc[0]
    assert row["sport"] == "NFL"
    assert row["season"] == 2025
    assert row["home_tricode"] == "BAL"
    assert bool(row["is_dome"]) is False


def test_nflverse_games_normalizer_marks_dome():
    pd = pytest.importorskip("pandas")
    from edge_equation.engines.football_core.data.nflverse_loader import (
        _normalize_games_df,
    )
    raw = pd.DataFrame([{
        "game_id": "x", "week": 1, "season_type": "REG",
        "gameday": "2025-09-04", "gametime": "20:20",
        "home_team": "ATL", "away_team": "TB",
        "stadium": "Mercedes-Benz", "stadium_id": "ATL00",
        "roof": "dome", "location": "Home",
    }])
    out = _normalize_games_df(raw, season=2025)
    assert bool(out.iloc[0]["is_dome"]) is True


def test_cfbd_games_normalizer_tags_ncaaf():
    from edge_equation.engines.football_core.data.cfbd_loader import (
        _normalize_games_payload,
    )
    df = _normalize_games_payload(
        [{"id": 401, "week": 3, "start_date": "2025-09-13T19:30:00Z",
          "home_team": "Alabama", "away_team": "Wisconsin",
          "venue": "Bryant-Denny", "venue_id": "T123",
          "neutral_site": False}],
        season=2025,
    )
    assert df.iloc[0]["sport"] == "NCAAF"
    assert df.iloc[0]["season"] == 2025
    assert df.iloc[0]["week"] == 3


def test_cfbd_lines_normalizer_emits_both_sides_for_spread():
    from edge_equation.engines.football_core.data.cfbd_loader import (
        _normalize_lines_payload,
    )
    df = _normalize_lines_payload(
        [{"id": 1, "lines": [{
            "provider": "DraftKings", "spread": -3.5,
            "overUnder": 51.5, "homeMoneyline": -180,
            "awayMoneyline": 150,
        }]}],
        season=2025,
    )
    sides = sorted(df["side"].tolist())
    # Spread (home/away) + Total (over/under) + ML (home/away) = 6 rows
    assert len(df) == 6
    assert "home" in sides and "away" in sides
    assert "over" in sides and "under" in sides


def test_odds_history_market_canonical():
    from edge_equation.engines.football_core.data.odds_history import (
        _market_canonical,
    )
    assert _market_canonical("spreads") == "Spread"
    assert _market_canonical("totals") == "Total"
    assert _market_canonical("h2h") == "ML"
    assert _market_canonical("garbage") is None


def test_odds_history_normalizer_flattens_payload():
    from edge_equation.engines.football_core.data.odds_history import (
        _normalize_historical_payload,
    )
    payload = {
        "timestamp": "2025-09-07T17:25:00Z",
        "data": [{
            "id": "evt1",
            "home_team": "Ravens", "away_team": "Chiefs",
            "bookmakers": [{
                "key": "draftkings",
                "markets": [{
                    "key": "spreads",
                    "outcomes": [
                        {"name": "Ravens", "point": -3.5, "price": -110},
                        {"name": "Chiefs", "point": 3.5, "price": -110},
                    ],
                }],
            }],
        }],
    }
    df = _normalize_historical_payload(payload)
    assert len(df) == 2
    assert set(df["side"]) == {"home", "away"}
    assert df.iloc[0]["book"] == "draftkings"


def test_weather_indoor_short_circuits():
    from edge_equation.engines.football_core.data.weather_history import (
        fetch_archive_weather,
    )
    snap = fetch_archive_weather(
        game_id="g1", sport="NFL",
        latitude=33.7, longitude=-84.4,
        kickoff_iso="2025-09-07T17:25:00Z",
        is_indoor=True,
    )
    assert snap.is_indoor is True
    assert snap.wind_speed_mph == 0.0
    assert snap.source == "indoor"


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    n_games: int = 0
    n_plays: int = 0
    n_lines: int = 0
    df: Any = None


class _FakeNflLoader:
    @staticmethod
    def fetch_games(*, season):
        import pandas as pd
        df = pd.DataFrame([{
            "game_id": "g1", "sport": "NFL", "season": season,
            "week": 1, "season_type": "REG",
            "event_date": "2025-09-04", "kickoff_ts": "2025-09-04 20:20",
            "home_team": "BAL", "away_team": "KC",
            "home_tricode": "BAL", "away_tricode": "KC",
            "venue": "M&T", "venue_code": "BAL00",
            "is_dome": False, "is_neutral_site": False,
        }])
        return _Result(n_games=1, df=df)

    @staticmethod
    def fetch_pbp(*, season):
        import pandas as pd
        return _Result(n_plays=42, df=pd.DataFrame([
            {"game_id": "g1", "play_id": str(i), "sport": "NFL"}
            for i in range(42)
        ]))


def test_nfl_orchestrator_checkpoints_each_op():
    pytest.importorskip("pandas")
    from edge_equation.engines.football_core.data.backfill_nfl import (
        backfill_season,
    )
    store = _FakeStore()
    result = backfill_season(
        season=2025, store=store,
        nfl_loader=_FakeNflLoader,
    )
    assert result.n_games_loaded == 1
    assert result.n_plays_loaded == 42
    ops = {c["op"] for c in store.checkpoints
            if c.get("error") is None}
    # games + plays + actuals all checkpointed
    assert {"games", "plays", "actuals"}.issubset(ops)


def test_nfl_orchestrator_skips_completed_ops_on_rerun():
    pytest.importorskip("pandas")
    from edge_equation.engines.football_core.data.backfill_nfl import (
        backfill_season,
    )
    from edge_equation.engines.football_core.data.checkpoints import (
        record_completion,
    )
    store = _FakeStore()
    # Pre-mark games as done.
    record_completion(
        store, sport="NFL", target_date="2025-01-01",
        op="games", rows_loaded=272,
    )
    result = backfill_season(
        season=2025, store=store, nfl_loader=_FakeNflLoader,
    )
    # Games op was skipped → n_games_loaded stays 0.
    assert result.n_games_loaded == 0
    assert result.n_skipped >= 1


def test_nfl_orchestrator_skip_plays_flag():
    pytest.importorskip("pandas")
    from edge_equation.engines.football_core.data.backfill_nfl import (
        backfill_season,
    )
    store = _FakeStore()
    result = backfill_season(
        season=2025, store=store, nfl_loader=_FakeNflLoader,
        skip_plays=True,
    )
    assert result.n_plays_loaded == 0


def test_nfl_orchestrator_records_failure_on_loader_error():
    from edge_equation.engines.football_core.data.backfill_nfl import (
        backfill_season,
    )

    class _BoomLoader:
        @staticmethod
        def fetch_games(*, season):
            raise RuntimeError("network down")
        @staticmethod
        def fetch_pbp(*, season):
            raise RuntimeError("network down")

    store = _FakeStore()
    result = backfill_season(
        season=2025, store=store, nfl_loader=_BoomLoader,
    )
    assert result.errors
    assert any("games" in e for e in result.errors)
    err_rows = [c for c in store.checkpoints if c.get("error")]
    assert err_rows, "failure should be checkpointed"


class _FakeCfbdLoader:
    @staticmethod
    def fetch_games(*, season):
        import pandas as pd
        return _Result(n_games=2, df=pd.DataFrame([
            {"game_id": f"c{i}", "sport": "NCAAF", "season": season,
             "week": 1, "season_type": "regu",
             "event_date": "2025-08-30", "kickoff_ts": "2025-08-30 19:00",
             "home_team": f"H{i}", "away_team": f"A{i}",
             "home_tricode": "HOM", "away_tricode": "AWY",
             "venue": "X", "venue_code": "V",
             "is_dome": False, "is_neutral_site": False}
            for i in range(2)
        ]))

    @staticmethod
    def fetch_plays(*, season, week):
        import pandas as pd
        return _Result(n_plays=10, df=pd.DataFrame([
            {"game_id": "c0", "play_id": f"{week}-{i}", "sport": "NCAAF"}
            for i in range(10)
        ]))

    @staticmethod
    def fetch_lines(*, season):
        import pandas as pd
        return _Result(n_lines=4, df=pd.DataFrame([
            {"game_id": "c0", "market": "Spread", "side": "home",
             "line_value": -3.5, "american_odds": -110.0,
             "book": "cfbd", "line_captured_at": "2025-08-30",
             "is_closing": False},
        ]))


def test_ncaaf_orchestrator_checkpoints_per_week_for_plays():
    pytest.importorskip("pandas")
    from edge_equation.engines.football_core.data.backfill_ncaaf import (
        backfill_season,
    )
    store = _FakeStore()
    result = backfill_season(
        season=2025, store=store,
        cfbd_loader=_FakeCfbdLoader,
        weeks=(1, 2, 3),
    )
    week_ops = [c["op"] for c in store.checkpoints
                if c.get("error") is None and c["op"].startswith("plays_w")]
    assert sorted(week_ops) == ["plays_w1", "plays_w2", "plays_w3"]
    assert result.n_plays_loaded == 30  # 3 weeks × 10 plays


def test_ncaaf_orchestrator_pulls_cfbd_lines_by_default():
    pytest.importorskip("pandas")
    from edge_equation.engines.football_core.data.backfill_ncaaf import (
        backfill_season,
    )
    store = _FakeStore()
    result = backfill_season(
        season=2025, store=store,
        cfbd_loader=_FakeCfbdLoader,
        weeks=(1,),
    )
    assert result.n_lines_loaded == 4
    cfbd_op = [c for c in store.checkpoints
                if c["op"] == "cfbd_lines" and c.get("error") is None]
    assert cfbd_op


def test_ncaaf_orchestrator_skips_plays_when_flag_set():
    pytest.importorskip("pandas")
    from edge_equation.engines.football_core.data.backfill_ncaaf import (
        backfill_season,
    )
    store = _FakeStore()
    result = backfill_season(
        season=2025, store=store,
        cfbd_loader=_FakeCfbdLoader,
        weeks=(1, 2),
        skip_plays=True,
    )
    assert result.n_plays_loaded == 0


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def test_diagnostics_renders_corpus_report():
    from edge_equation.engines.football_core.data.diagnostics import (
        CorpusReport,
    )
    r = CorpusReport(
        sport="NFL", season=2025, n_games=272,
        n_plays=48000, n_actuals=272, n_weather=240,
        weather_coverage_pct=88.2, n_completed_ops=5,
    )
    out = r.render()
    assert "NFL" in out
    assert "272" in out
    assert "88.2%" in out
    assert "Backfill checkpoints" in out


def test_diagnostics_module_exports_run():
    from edge_equation.engines.football_core.data import diagnostics
    assert callable(diagnostics.run_diagnostics)
    assert callable(diagnostics.main)
