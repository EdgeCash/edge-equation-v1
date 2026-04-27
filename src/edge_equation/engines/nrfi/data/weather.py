"""Open-Meteo weather client.

Two endpoints:
* Forecast (`api.open-meteo.com/v1/forecast`) — current daily/realtime use.
* Archive  (`archive-api.open-meteo.com/v1/archive`) — backtest reconstruction.

We intentionally request hourly variables and snap to the hour nearest
first pitch so backtests reconstruct the same value the daily run would
have seen.

No API key required. Polite rate-limited via `nrfi.utils.rate_limit`.
Air density derived from temperature, humidity, and (approx) pressure
using the simplified ideal-gas formulation — a small but real edge for
HR carry & ball flight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import APIConfig
from ..utils.logging import get_logger
from ..utils.rate_limit import global_limiter

log = get_logger(__name__)

# Standard sea-level pressure (hPa)
_P0_HPA = 1013.25


@dataclass(frozen=True)
class WeatherSnapshot:
    """Weather observation snapped to the first-pitch hour."""

    temperature_f: float
    wind_speed_mph: float
    wind_dir_deg: float          # meteorological — direction wind is FROM
    humidity_pct: float
    dew_point_f: float
    air_density_kg_m3: float     # rho — proxy for HR carry
    precip_prob: float
    source: str                  # 'forecast' | 'archive'


def _altitude_pressure_hpa(altitude_ft: int) -> float:
    """Barometric approximation good to ~1% at MLB altitudes."""
    altitude_m = altitude_ft * 0.3048
    return _P0_HPA * math.exp(-altitude_m / 8434.5)


def _air_density(temp_f: float, humidity_pct: float, altitude_ft: int) -> float:
    """Compute air density (kg/m^3) using the partial-pressure formulation.

    Carry distance scales roughly inversely with density — Coors at 5197ft,
    95F, 30%RH is ~10% less dense than Fenway at 60F, 80%RH at sea level.
    """
    t_c = (temp_f - 32.0) * 5.0 / 9.0
    t_k = t_c + 273.15

    # Saturation vapor pressure (Tetens) in hPa
    sat_vp = 6.1078 * math.exp((17.27 * t_c) / (t_c + 237.3))
    p_v = sat_vp * (humidity_pct / 100.0)
    p_total = _altitude_pressure_hpa(altitude_ft)
    p_d = max(0.1, p_total - p_v)

    # rho = (Pd / (Rd*T)) + (Pv / (Rv*T)), Rd=287.05, Rv=461.495 J/(kg·K)
    rho = (p_d * 100.0) / (287.05 * t_k) + (p_v * 100.0) / (461.495 * t_k)
    return rho


def _index_for_hour(times: list[str], target_iso_hour: str) -> int:
    """Find the index of the hourly slot closest to target_iso_hour."""
    target = datetime.fromisoformat(target_iso_hour).replace(tzinfo=timezone.utc)
    best_idx, best_delta = 0, float("inf")
    for i, t in enumerate(times):
        dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        delta = abs((dt - target).total_seconds())
        if delta < best_delta:
            best_delta, best_idx = delta, i
    return best_idx


class WeatherClient:
    """Open-Meteo client. Pass an APIConfig to override endpoints."""

    def __init__(self, api: APIConfig | None = None):
        self.api = api or APIConfig()
        self.limiter = global_limiter(self.api.requests_per_minute)
        self._http = httpx.Client(
            timeout=self.api.request_timeout_s,
            headers={"User-Agent": self.api.user_agent},
        )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    def forecast(self, lat: float, lon: float, target_iso_hour: str,
                 altitude_ft: int) -> Optional[WeatherSnapshot]:
        """Return forecast snapshot for the hour nearest target_iso_hour."""
        params = {
            "latitude": lat, "longitude": lon,
            "hourly": ",".join([
                "temperature_2m", "windspeed_10m", "winddirection_10m",
                "relativehumidity_2m", "dewpoint_2m", "precipitation_probability",
            ]),
            "temperature_unit": "fahrenheit",
            "windspeed_unit": "mph",
            "timezone": "UTC",
            "forecast_days": 3,
        }
        return self._fetch(self.api.open_meteo_forecast, params,
                           target_iso_hour, altitude_ft, source="forecast")

    def archive(self, lat: float, lon: float, target_iso_hour: str,
                altitude_ft: int) -> Optional[WeatherSnapshot]:
        """Backtest variant: pulls from the historical archive endpoint."""
        date = target_iso_hour[:10]
        params = {
            "latitude": lat, "longitude": lon,
            "start_date": date, "end_date": date,
            "hourly": ",".join([
                "temperature_2m", "windspeed_10m", "winddirection_10m",
                "relativehumidity_2m", "dewpoint_2m", "precipitation",
            ]),
            "temperature_unit": "fahrenheit",
            "windspeed_unit": "mph",
            "timezone": "UTC",
        }
        return self._fetch(self.api.open_meteo_archive, params,
                           target_iso_hour, altitude_ft, source="archive")

    # ------------------------------------------------------------------
    def _fetch(self, url: str, params: dict, target_iso_hour: str,
               altitude_ft: int, source: str) -> Optional[WeatherSnapshot]:
        with self.limiter.acquire():
            try:
                r = self._http.get(url, params=params)
                r.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("Open-Meteo fetch failed (%s): %s", source, e)
                return None
        data = r.json().get("hourly", {})
        times = data.get("time") or []
        if not times:
            return None
        idx = _index_for_hour(times, target_iso_hour)

        temp_f   = float(data.get("temperature_2m",   [None])[idx] or 70.0)
        wind_mph = float(data.get("windspeed_10m",    [None])[idx] or 0.0)
        wind_dir = float(data.get("winddirection_10m",[None])[idx] or 0.0)
        humid    = float(data.get("relativehumidity_2m", [None])[idx] or 50.0)
        dew_f    = float(data.get("dewpoint_2m",      [None])[idx] or 55.0)
        precip   = data.get("precipitation_probability") or data.get("precipitation") or [0.0]
        precip_v = float(precip[idx] or 0.0) if idx < len(precip) else 0.0
        if "precipitation" in data and "precipitation_probability" not in data:
            # archive returns mm — convert to a coarse 0-100 prob proxy
            precip_v = min(100.0, precip_v * 10.0)

        rho = _air_density(temp_f, humid, altitude_ft)

        return WeatherSnapshot(
            temperature_f=temp_f,
            wind_speed_mph=wind_mph,
            wind_dir_deg=wind_dir,
            humidity_pct=humid,
            dew_point_f=dew_f,
            air_density_kg_m3=rho,
            precip_prob=precip_v,
            source=source,
        )


def wind_orientation_factor(wind_dir_deg: float, cf_orientation_deg: int) -> float:
    """Project wind onto the home plate → CF axis.

    Returns a signed scalar in [-1, 1]:
        +1 = pure tailwind (blowing out to CF)
        -1 = pure headwind (blowing in from CF)
         0 = pure crosswind
    Multiply by speed (mph) downstream to get directional carry.
    """
    # Open-Meteo uses meteorological convention (FROM direction).
    # Convert to TO direction for projection.
    wind_to = (wind_dir_deg + 180.0) % 360.0
    delta = math.radians(wind_to - cf_orientation_deg)
    return math.cos(delta)
