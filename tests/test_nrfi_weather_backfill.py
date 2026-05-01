"""Tests for the historical weather backfill module.

Uses a fake NRFIStore + a fake WeatherClient — no DuckDB, no
network. Verifies idempotent skip, force re-fetch, unknown-park
handling, and the upsert payload shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeWx:
    source: str = "open-meteo-archive"
    temperature_f: float = 72.0
    wind_speed_mph: float = 5.0
    wind_dir_deg: float = 180.0
    humidity_pct: float = 60.0
    dew_point_f: float = 60.0
    air_density_kg_m3: float = 1.20
    precip_prob: float = 0.0


_UNSET = object()


class _FakeWeatherClient:
    def __init__(self, return_value=_UNSET, raise_on=None):
        # Sentinel pattern so `return_value=None` is distinguishable
        # from the default (= return a happy WeatherSnapshot).
        self._return = _FakeWx() if return_value is _UNSET else return_value
        self._raise_on = set(raise_on or ())
        self.calls: list[tuple[float, float, str, int]] = []

    def archive(self, lat, lon, target_iso_hour, altitude_ft):
        self.calls.append((lat, lon, target_iso_hour, altitude_ft))
        if (lat, lon) in self._raise_on:
            raise RuntimeError("simulated open-meteo failure")
        return self._return

    def close(self):
        pass


class _FakeStore:
    """Just-enough DuckDB surface: query_df + upsert."""

    def __init__(self, games):
        # `games` is a list of {"game_pk", "first_pitch_ts", "venue_code",
        # "has_weather"} dicts the LEFT-JOIN query would produce.
        self._games = games
        self.upserts: list[tuple[str, list[dict]]] = []

    def query_df(self, sql, params=None):
        import pandas as pd
        return pd.DataFrame(self._games)

    def upsert(self, table, rows):
        rows = list(rows)
        self.upserts.append((table, rows))
        return len(rows)


# ---------------------------------------------------------------------------
# Module-level skip when sklearn missing — the weather backfill itself
# doesn't need sklearn but the `data` package's downstream imports do.
# ---------------------------------------------------------------------------


pytest.importorskip("pandas")


# ---------------------------------------------------------------------------
# Idempotent skip: games with already-filled weather are not re-fetched
# ---------------------------------------------------------------------------


def test_already_filled_games_are_skipped_by_default():
    from edge_equation.engines.nrfi.data.weather_backfill import backfill_weather

    games = [
        {"game_pk": 1, "first_pitch_ts": "2025-04-01T19:05:00",
         "venue_code": "BAL", "has_weather": 1},
        {"game_pk": 2, "first_pitch_ts": "2025-04-01T19:05:00",
         "venue_code": "BAL", "has_weather": 0},
    ]
    store = _FakeStore(games)
    client = _FakeWeatherClient()
    report = backfill_weather(store=store, weather_client=client)

    assert report.n_games_already_filled == 1
    assert report.n_weather_persisted == 1
    assert len(client.calls) == 1   # only the empty one was fetched


def test_force_flag_refetches_already_filled_games():
    from edge_equation.engines.nrfi.data.weather_backfill import backfill_weather

    games = [
        {"game_pk": 1, "first_pitch_ts": "2025-04-01T19:05:00",
         "venue_code": "BAL", "has_weather": 1},
    ]
    store = _FakeStore(games)
    client = _FakeWeatherClient()
    report = backfill_weather(store=store, weather_client=client, force=True)
    assert report.n_weather_persisted == 1
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Unknown-park / bad timestamp handling
# ---------------------------------------------------------------------------


def test_unknown_park_code_skipped_without_failure():
    from edge_equation.engines.nrfi.data.weather_backfill import backfill_weather
    games = [
        {"game_pk": 1, "first_pitch_ts": "2025-04-01T19:05:00",
         "venue_code": "ZZZ", "has_weather": 0},
    ]
    store = _FakeStore(games)
    client = _FakeWeatherClient()
    report = backfill_weather(store=store, weather_client=client)
    assert report.n_unknown_park == 1
    assert report.n_weather_persisted == 0
    assert len(client.calls) == 0


def test_missing_first_pitch_ts_skipped():
    from edge_equation.engines.nrfi.data.weather_backfill import backfill_weather
    games = [
        {"game_pk": 1, "first_pitch_ts": None,
         "venue_code": "BAL", "has_weather": 0},
    ]
    store = _FakeStore(games)
    client = _FakeWeatherClient()
    report = backfill_weather(store=store, weather_client=client)
    assert report.n_unknown_park == 1
    assert report.n_weather_persisted == 0


# ---------------------------------------------------------------------------
# Failure paths — Open-Meteo raises, returns None
# ---------------------------------------------------------------------------


def test_open_meteo_exception_counted_as_failure():
    from edge_equation.engines.nrfi.data.park_factors import park_for
    from edge_equation.engines.nrfi.data.weather_backfill import backfill_weather

    park = park_for("BAL")
    games = [
        {"game_pk": 1, "first_pitch_ts": "2025-04-01T19:05:00",
         "venue_code": "BAL", "has_weather": 0},
    ]
    store = _FakeStore(games)
    client = _FakeWeatherClient(raise_on={(park.lat, park.lon)})
    report = backfill_weather(store=store, weather_client=client)
    assert report.n_weather_failed == 1
    assert report.n_weather_persisted == 0


def test_open_meteo_returns_none_counted_as_failure():
    from edge_equation.engines.nrfi.data.weather_backfill import backfill_weather
    games = [
        {"game_pk": 1, "first_pitch_ts": "2025-04-01T19:05:00",
         "venue_code": "BAL", "has_weather": 0},
    ]
    store = _FakeStore(games)
    client = _FakeWeatherClient(return_value=None)
    report = backfill_weather(store=store, weather_client=client)
    assert report.n_weather_failed == 1
    assert report.n_weather_persisted == 0


# ---------------------------------------------------------------------------
# Upsert payload shape
# ---------------------------------------------------------------------------


def test_upsert_payload_carries_expected_columns():
    from edge_equation.engines.nrfi.data.weather_backfill import backfill_weather
    games = [
        {"game_pk": 12345, "first_pitch_ts": "2025-04-01T19:05:00",
         "venue_code": "BAL", "has_weather": 0},
    ]
    store = _FakeStore(games)
    client = _FakeWeatherClient()
    backfill_weather(store=store, weather_client=client)

    assert store.upserts, "expected at least one upsert"
    table, rows = store.upserts[0]
    assert table == "weather"
    row = rows[0]
    for col in (
        "game_pk", "source", "as_of_ts", "temperature_f",
        "wind_speed_mph", "wind_dir_deg", "humidity_pct",
        "dew_point_f", "air_density", "precip_prob", "roof_open",
    ):
        assert col in row, f"missing column {col!r} in upsert payload"
    assert row["game_pk"] == 12345


# ---------------------------------------------------------------------------
# Iso-hour normalization
# ---------------------------------------------------------------------------


def test_to_iso_hour_handles_string_timestamp():
    from edge_equation.engines.nrfi.data.weather_backfill import _to_iso_hour
    assert _to_iso_hour("2025-04-01T19:05:00Z") == "2025-04-01T19:05:00"
    assert _to_iso_hour("2025-04-01T19:05:00") == "2025-04-01T19:05:00"


def test_to_iso_hour_handles_datetime_object():
    from datetime import datetime
    from edge_equation.engines.nrfi.data.weather_backfill import _to_iso_hour
    out = _to_iso_hour(datetime(2025, 4, 1, 19, 5, 0))
    assert out == "2025-04-01T19:05:00"


def test_to_iso_hour_returns_none_for_garbage():
    from edge_equation.engines.nrfi.data.weather_backfill import _to_iso_hour
    assert _to_iso_hour(None) is None
    assert _to_iso_hour("") is None
