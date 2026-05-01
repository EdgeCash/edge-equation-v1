"""Historical weather backfill — walks the games table directly.

PR #101's weather persistence hook only fires when
`reconstruct_features_for_date` runs. The corpus orchestrator's
`skip_completed=True` skips features for dates that already have a
checkpoint, so the historical 4,000+ games never get their weather
populated retroactively. The first real run of the weather backfill
workflow proved this: missing weather rate moved from 100% → 99.1%
because only the 3 newly-built dates got weather.

This module fills that gap. It walks the `games` table, looks up each
game's park (lat/lon/altitude), fetches the Open-Meteo *archive*
endpoint for the kickoff hour, and upserts the snapshot into the
`weather` table. Idempotent: games that already have a weather row
are skipped unless `force=True`.

CLI
~~~

::

    python -m edge_equation.engines.nrfi.data.weather_backfill \\
        --from 2025-01-01 --to 2026-04-30

    # Force re-fetch of every game (e.g. after a weather-source upgrade)
    python -m edge_equation.engines.nrfi.data.weather_backfill --force
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional, Sequence

from edge_equation.utils.logging import get_logger

from ..config import NRFIConfig, get_default_config
from .park_factors import park_for
from .storage import NRFIStore
from .weather import WeatherClient

log = get_logger(__name__)


@dataclass
class WeatherBackfillReport:
    n_games_total: int = 0
    n_games_already_filled: int = 0
    n_games_attempted: int = 0
    n_weather_persisted: int = 0
    n_weather_failed: int = 0
    n_unknown_park: int = 0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "NRFI historical weather backfill",
            "-" * 56,
            f"  games in window           {self.n_games_total}",
            f"  already had weather       {self.n_games_already_filled}",
            f"  attempted to fetch        {self.n_games_attempted}",
            f"  persisted                 {self.n_weather_persisted}",
            f"  open-meteo failures       {self.n_weather_failed}",
            f"  unknown park (skipped)    {self.n_unknown_park}",
            f"  elapsed                   {self.elapsed_s:.1f}s",
        ]
        if self.errors:
            lines.append("  recent errors:")
            for e in self.errors[:5]:
                lines.append(f"    {e[:120]}")
        return "\n".join(lines)


def backfill_weather(
    *,
    store: Optional[NRFIStore] = None,
    config: Optional[NRFIConfig] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    force: bool = False,
    pause_seconds: float = 0.0,
    weather_client: Optional[WeatherClient] = None,
    progress_callback=None,
) -> WeatherBackfillReport:
    """Fill the `weather` table for every game in [start_date, end_date].

    Parameters
    ----------
    force : When True, re-fetch even for games that already have a
        weather row. Default False = idempotent skip.
    pause_seconds : Optional throttle between Open-Meteo requests.
        The archive endpoint is generous; 0 is fine for small windows,
        bump to 0.1-0.5 for full-corpus rebuilds to be polite.
    weather_client : Test hook. When None, a default WeatherClient is
        constructed and closed at the end.
    """
    cfg = (config or get_default_config()).resolve_paths()
    store = store or NRFIStore(cfg.duckdb_path)
    own_client = weather_client is None
    client = weather_client or WeatherClient()

    report = WeatherBackfillReport()
    started = time.monotonic()

    try:
        rows = _games_to_backfill(
            store, start_date=start_date, end_date=end_date,
            include_already_filled=force,
        )
        report.n_games_total = len(rows)

        for i, game in enumerate(rows):
            if progress_callback is not None:
                progress_callback(i, len(rows), game)

            if game["already_filled"] and not force:
                report.n_games_already_filled += 1
                continue

            park = _safe_park_for(game["venue_code"])
            if park is None:
                report.n_unknown_park += 1
                continue

            target_iso = _to_iso_hour(game["first_pitch_ts"])
            if target_iso is None:
                report.n_unknown_park += 1   # bucket bad timestamps too
                continue

            report.n_games_attempted += 1
            try:
                wx = client.archive(
                    park.lat, park.lon, target_iso, park.altitude_ft,
                )
            except Exception as e:
                report.n_weather_failed += 1
                report.errors.append(f"{game['game_pk']}: {e}")
                continue

            if wx is None:
                report.n_weather_failed += 1
                continue

            try:
                store.upsert("weather", [{
                    "game_pk": int(game["game_pk"]),
                    "source": wx.source,
                    "as_of_ts": target_iso,
                    "temperature_f": float(wx.temperature_f),
                    "wind_speed_mph": float(wx.wind_speed_mph),
                    "wind_dir_deg": float(wx.wind_dir_deg),
                    "humidity_pct": float(wx.humidity_pct),
                    "dew_point_f": float(wx.dew_point_f),
                    "air_density": float(wx.air_density_kg_m3),
                    "precip_prob": float(wx.precip_prob),
                    "roof_open": None,
                }])
                report.n_weather_persisted += 1
            except Exception as e:
                report.n_weather_failed += 1
                report.errors.append(f"{game['game_pk']} upsert: {e}")
                continue

            if pause_seconds > 0:
                time.sleep(pause_seconds)

        report.elapsed_s = time.monotonic() - started
    finally:
        if own_client:
            client.close()

    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _games_to_backfill(
    store: NRFIStore,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    include_already_filled: bool,
) -> list[dict]:
    """Pull every game in the window with a flag for weather presence.

    Joins games LEFT JOIN weather ON game_pk so we know in one query
    which rows already have weather (for idempotent skip).
    """
    where_clauses: list[str] = []
    params: list = []
    if start_date:
        where_clauses.append("g.game_date >= ?")
        params.append(start_date)
    if end_date:
        where_clauses.append("g.game_date <= ?")
        params.append(end_date)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT
            g.game_pk,
            g.first_pitch_ts,
            g.venue_code,
            CASE WHEN w.game_pk IS NULL THEN 0 ELSE 1 END AS has_weather
        FROM games g
        LEFT JOIN weather w ON w.game_pk = g.game_pk
        {where_sql}
        ORDER BY g.game_date
    """
    df = store.query_df(sql, tuple(params))
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, r in df.iterrows():
        out.append({
            "game_pk": int(r["game_pk"]),
            "first_pitch_ts": r.get("first_pitch_ts"),
            "venue_code": str(r.get("venue_code") or ""),
            "already_filled": bool(int(r.get("has_weather") or 0) == 1),
        })
    return out


def _safe_park_for(venue_code: str):
    if not venue_code:
        return None
    try:
        return park_for(venue_code)
    except Exception:
        return None


def _to_iso_hour(value) -> Optional[str]:
    """Normalize a DuckDB timestamp / string into the ISO hour string
    Open-Meteo's archive endpoint expects (YYYY-MM-DDTHH:MM:SS)."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        return s.replace("Z", "+00:00")[:19]
    try:
        if hasattr(value, "isoformat"):
            return value.isoformat()[:19]
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill historical weather for every NRFI game in the corpus.",
    )
    parser.add_argument("--from", dest="start_date", default=None)
    parser.add_argument("--to", dest="end_date", default=None)
    parser.add_argument("--force", action="store_true",
                          help="Re-fetch even for games that already have weather.")
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    def _progress(i, total, game):
        if args.quiet:
            return
        if total < 50 or i % max(1, total // 20) == 0:
            print(f"  [{i+1}/{total}] game_pk={game['game_pk']} venue={game['venue_code']}")

    report = backfill_weather(
        start_date=args.start_date,
        end_date=args.end_date,
        force=args.force,
        pause_seconds=args.pause_seconds,
        progress_callback=None if args.quiet else _progress,
    )
    print(report.summary())
    return 0 if report.n_weather_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
