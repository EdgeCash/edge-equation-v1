"""Per-game weather history via Open-Meteo's archive endpoint.

Free, no API key required. Same source the NRFI engine uses for
first-inning weather; we reuse the shape but query the archive
endpoint with the kickoff timestamp so we get the actual conditions
the game was played in (not a forecast).

Endpoint
~~~~~~~~

``GET https://archive-api.open-meteo.com/v1/archive``

Query params:
* ``latitude`` / ``longitude`` — venue coordinates
* ``start_date`` / ``end_date`` — game date (UTC)
* ``hourly`` — temperature_2m, wind_speed_10m, wind_direction_10m,
  relative_humidity_2m, precipitation_probability

We pick the hour matching `kickoff_ts` and return one snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_TIMEOUT_S = 30.0


class LoaderError(RuntimeError):
    """Raised when the loader can't return data."""


@dataclass(frozen=True)
class WeatherSnapshot:
    """One captured weather row, ready for `football_weather` upsert."""
    game_id: str
    sport: str
    source: str = "open-meteo-archive"
    captured_at: str = ""
    temperature_f: float = 0.0
    wind_speed_mph: float = 0.0
    wind_dir_deg: float = 0.0
    humidity_pct: float = 0.0
    precipitation_prob: float = 0.0
    is_indoor: bool = False


def fetch_archive_weather(
    *, game_id: str, sport: str,
    latitude: float, longitude: float,
    kickoff_iso: str,
    is_indoor: bool = False,
    http_client=None,
) -> WeatherSnapshot:
    """Pull the kickoff-hour weather snapshot for one game.

    For indoor / dome games we short-circuit and return a "still air"
    snapshot — Open-Meteo would return surface weather the game
    didn't experience, which would corrupt the projection layer.
    """
    if is_indoor:
        return WeatherSnapshot(
            game_id=game_id, sport=sport, source="indoor",
            captured_at=kickoff_iso[:19],
            temperature_f=70.0, wind_speed_mph=0.0,
            wind_dir_deg=0.0, humidity_pct=50.0,
            precipitation_prob=0.0, is_indoor=True,
        )

    target_date = kickoff_iso[:10]
    target_hour = int(kickoff_iso[11:13]) if len(kickoff_iso) >= 13 else 19

    params = {
        "latitude": latitude, "longitude": longitude,
        "start_date": target_date, "end_date": target_date,
        "hourly": (
            "temperature_2m,wind_speed_10m,wind_direction_10m,"
            "relative_humidity_2m,precipitation_probability"
        ),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "UTC",
    }

    owns_client = http_client is None
    if owns_client:
        try:
            import httpx
        except ImportError as e:  # pragma: no cover
            raise LoaderError(
                "httpx is required for Open-Meteo fetch — install via "
                "`pip install -e .[nrfi]`",
            ) from e
        http_client = httpx.Client(timeout=DEFAULT_TIMEOUT_S)
    try:
        try:
            resp = http_client.get(OPEN_METEO_ARCHIVE, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise LoaderError(f"Open-Meteo archive fetch failed: {e}") from e
    finally:
        if owns_client:
            http_client.close()

    hourly = data.get("hourly", {}) or {}
    times: list[str] = list(hourly.get("time", []) or [])

    # Find the closest hour to kickoff. `times` is hourly UTC ISO.
    pick_idx = 0
    for i, t in enumerate(times):
        if len(t) >= 13 and int(t[11:13]) <= target_hour:
            pick_idx = i

    def _val(key: str) -> float:
        arr = hourly.get(key, []) or []
        if pick_idx < len(arr) and arr[pick_idx] is not None:
            return float(arr[pick_idx])
        return 0.0

    return WeatherSnapshot(
        game_id=game_id, sport=sport,
        captured_at=times[pick_idx] if pick_idx < len(times) else target_date,
        temperature_f=_val("temperature_2m"),
        wind_speed_mph=_val("wind_speed_10m"),
        wind_dir_deg=_val("wind_direction_10m"),
        humidity_pct=_val("relative_humidity_2m"),
        precipitation_prob=_val("precipitation_probability"),
        is_indoor=False,
    )
