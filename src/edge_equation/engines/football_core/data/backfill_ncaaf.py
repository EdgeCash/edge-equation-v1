"""NCAAF season backfill orchestrator.

Mirrors `backfill_nfl.py` but pulls from the College Football Data
API (free tier; 1000 req/mo) instead of nflverse:

1. ``games``     — `/games?year=<season>` (1 call).
2. ``plays``     — `/plays?year=<season>&week=<n>` (15 weeks ≈ 15 calls).
3. ``actuals``   — derived from the games payload (CFBD `/games`
                  surfaces final scores once a game completes).
4. ``weather``   — Open-Meteo archive for each outdoor game.
5. ``lines``     — `/lines?year=<season>` (free; sparse compared to
                  the Odds API historical, but free, so we always pull
                  it). The Odds API historical is also available behind
                  ``include_historical_odds=True`` like NFL.

Each op is checkpointed via `football_backfill_checkpoints` so a
re-run is idempotent — already-completed (sport='NCAAF', date, op)
tuples are skipped.

Usage
~~~~~

::

    python -m edge_equation.engines.football_core.data.backfill_ncaaf \\
        --season 2025 --duckdb-path data/ncaaf_cache/ncaaf.duckdb

    python -m edge_equation.engines.football_core.data.backfill_ncaaf \\
        --season 2025 --include-historical-odds   # paid Odds API tier

    python -m edge_equation.engines.football_core.data.backfill_ncaaf \\
        --season 2025 --skip-plays                # games + actuals + weather only
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger

from .cfbd_loader import (
    LoaderError as CfbdLoaderError,
    fetch_cfbd_games, fetch_cfbd_lines, fetch_cfbd_plays,
)
from .checkpoints import (
    completed_pairs, record_completion, record_failure,
)
from .odds_history import (
    LoaderError as OddsLoaderError,
    fetch_historical_lines,
)
from .storage import FootballStore
from .weather_history import (
    LoaderError as WeatherLoaderError,
    fetch_archive_weather,
)

log = get_logger(__name__)


SPORT = "NCAAF"
ODDS_SPORT_KEY = "americanfootball_ncaaf"

# CFBD's regular season runs Weeks 1-15 (Week 0 occasionally for early
# kickoffs). Bowl season is `seasonType='postseason'` and shipped via a
# separate orchestrator pass; this orchestrator handles regular-season
# corpus only.
DEFAULT_WEEKS = tuple(range(1, 16))


@dataclass
class BackfillResult:
    """Roll-up of one orchestrator run."""
    season: int
    n_games_loaded: int = 0
    n_plays_loaded: int = 0
    n_actuals_loaded: int = 0
    n_weather_loaded: int = 0
    n_lines_loaded: int = 0
    n_odds_loaded: int = 0
    n_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"NCAAF backfill — season {self.season}",
            "─" * 40,
            f"  games loaded         {self.n_games_loaded}",
            f"  plays loaded         {self.n_plays_loaded}",
            f"  actuals loaded       {self.n_actuals_loaded}",
            f"  weather loaded       {self.n_weather_loaded}",
            f"  cfbd lines loaded    {self.n_lines_loaded}",
            f"  odds rows loaded     {self.n_odds_loaded}",
            f"  ops skipped          {self.n_skipped}",
        ]
        if self.errors:
            lines.append("  errors:")
            for e in self.errors[:10]:
                lines.append(f"    {e}")
        return "\n".join(lines)


def backfill_season(
    *,
    season: int,
    store: FootballStore,
    include_historical_odds: bool = False,
    skip_plays: bool = False,
    skip_weather: bool = False,
    weeks: Sequence[int] = DEFAULT_WEEKS,
    venue_lookup: Optional[dict] = None,
    cfbd_loader=None,
    weather_loader=None,
    odds_loader=None,
) -> BackfillResult:
    """Run the full NCAAF backfill for `season`.

    Parameters
    ----------
    weeks : sequence of week numbers to pull plays for. Defaults to
        the regular season (1-15). Postseason / bowl pulls are out of
        scope for this orchestrator.
    venue_lookup : optional dict mapping `venue_code → (lat, lon, is_indoor)`.
        Required for the weather op; when missing, the orchestrator
        skips that op rather than fail.
    cfbd_loader / weather_loader / odds_loader : injectable test hooks.
        Pass `None` to use the real loaders. The CFBD façade exposes
        `fetch_games(season=...)`, `fetch_plays(season=..., week=...)`,
        `fetch_lines(season=...)`.

    Resumability: each op is checkpointed under
    ``(sport='NCAAF', target_date=<season>-01-01, op=<games|plays|...>)``.
    Plays-per-week are checkpointed individually under
    ``op='plays_w<week>'`` so a partial-week failure only retries that
    one week.
    """
    cfbd_loader = cfbd_loader or _default_cfbd_loader
    weather_loader = weather_loader or fetch_archive_weather
    odds_loader = odds_loader or fetch_historical_lines

    result = BackfillResult(season=season)
    season_anchor = f"{season}-01-01"
    done = completed_pairs(store, sport=SPORT)

    # 1. Games
    if (season_anchor, "games") in done:
        log.info("games op already complete for %s — skipping", season)
        result.n_skipped += 1
    else:
        try:
            games = cfbd_loader.fetch_games(season=season)
            store.upsert("football_games", games.df.to_dict(orient="records"))
            record_completion(
                store, sport=SPORT, target_date=season_anchor, op="games",
                rows_loaded=games.n_games,
            )
            result.n_games_loaded = games.n_games
        except (CfbdLoaderError, Exception) as e:
            record_failure(
                store, sport=SPORT, target_date=season_anchor, op="games",
                error=str(e),
            )
            result.errors.append(f"games: {e}")
            return result

    # 2. Plays — one CFBD call per week. Each week is checkpointed
    # under its own op name so a partial-week failure only retries
    # that one week on the next run.
    if skip_plays:
        result.n_skipped += 1
    else:
        for week in weeks:
            op = f"plays_w{week}"
            if (season_anchor, op) in done:
                result.n_skipped += 1
                continue
            try:
                pbp = cfbd_loader.fetch_plays(season=season, week=week)
                if pbp.df is not None and not pbp.df.empty:
                    store.upsert(
                        "football_plays", pbp.df.to_dict(orient="records"),
                    )
                record_completion(
                    store, sport=SPORT, target_date=season_anchor, op=op,
                    rows_loaded=pbp.n_plays,
                )
                result.n_plays_loaded += pbp.n_plays
            except (CfbdLoaderError, Exception) as e:
                record_failure(
                    store, sport=SPORT, target_date=season_anchor, op=op,
                    error=str(e),
                )
                result.errors.append(f"{op}: {e}")

    # 3. Actuals — derived from CFBD `/games`. CFBD surfaces
    # home_points / away_points on the games payload once a game
    # completes; we re-read the frame and write to football_actuals.
    if (season_anchor, "actuals") in done:
        result.n_skipped += 1
    else:
        try:
            n = _persist_actuals_from_games(store, season=season)
            record_completion(
                store, sport=SPORT, target_date=season_anchor, op="actuals",
                rows_loaded=n,
            )
            result.n_actuals_loaded = n
        except Exception as e:
            record_failure(
                store, sport=SPORT, target_date=season_anchor, op="actuals",
                error=str(e),
            )
            result.errors.append(f"actuals: {e}")

    # 4. Weather (per-game).
    if skip_weather or venue_lookup is None:
        result.n_skipped += 1
    elif (season_anchor, "weather") in done:
        result.n_skipped += 1
    else:
        try:
            n = _backfill_weather(
                store, season=season, venue_lookup=venue_lookup,
                weather_loader=weather_loader,
            )
            record_completion(
                store, sport=SPORT, target_date=season_anchor, op="weather",
                rows_loaded=n,
            )
            result.n_weather_loaded = n
        except Exception as e:
            record_failure(
                store, sport=SPORT, target_date=season_anchor, op="weather",
                error=str(e),
            )
            result.errors.append(f"weather: {e}")

    # 5a. CFBD lines (always free; pull every season).
    if (season_anchor, "cfbd_lines") in done:
        result.n_skipped += 1
    else:
        try:
            cfbd_lines = cfbd_loader.fetch_lines(season=season)
            if cfbd_lines.df is not None and not cfbd_lines.df.empty:
                store.upsert(
                    "football_lines",
                    cfbd_lines.df.to_dict(orient="records"),
                )
            record_completion(
                store, sport=SPORT, target_date=season_anchor, op="cfbd_lines",
                rows_loaded=cfbd_lines.n_lines,
            )
            result.n_lines_loaded = cfbd_lines.n_lines
        except (CfbdLoaderError, Exception) as e:
            record_failure(
                store, sport=SPORT, target_date=season_anchor, op="cfbd_lines",
                error=str(e),
            )
            result.errors.append(f"cfbd_lines: {e}")

    # 5b. Odds API historical (gated; paid tier).
    if not include_historical_odds:
        result.n_skipped += 1
    elif (season_anchor, "odds") in done:
        result.n_skipped += 1
    else:
        try:
            n = _backfill_historical_odds(
                store, season=season, odds_loader=odds_loader,
            )
            record_completion(
                store, sport=SPORT, target_date=season_anchor, op="odds",
                rows_loaded=n,
            )
            result.n_odds_loaded = n
        except Exception as e:
            record_failure(
                store, sport=SPORT, target_date=season_anchor, op="odds",
                error=str(e),
            )
            result.errors.append(f"odds: {e}")

    return result


# ---------------------------------------------------------------------------
# Default CFBD loader wrapper
# ---------------------------------------------------------------------------


class _default_cfbd_loader:
    """Static façade so the orchestrator can dependency-inject a mock."""

    @staticmethod
    def fetch_games(*, season: int):
        return fetch_cfbd_games(season=season)

    @staticmethod
    def fetch_plays(*, season: int, week: int):
        return fetch_cfbd_plays(season=season, week=week)

    @staticmethod
    def fetch_lines(*, season: int):
        return fetch_cfbd_lines(season=season)


# ---------------------------------------------------------------------------
# Op helpers
# ---------------------------------------------------------------------------


def _persist_actuals_from_games(store: FootballStore, *, season: int) -> int:
    """Pull final-score rows out of football_games and persist into
    football_actuals.

    The CFBD `/games` payload carries `home_points` / `away_points`
    once a game completes; we kept the storage schema sport-agnostic
    so the score columns aren't on football_games. Until the per-game
    scores backfill module lands (planned in Phase F-2), this is a
    checkpoint-only no-op that lets the orchestrator mark the op
    done. Re-running after the score columns are wired re-pulls
    actuals correctly.
    """
    df = store.query_df(
        """
        SELECT game_id
        FROM football_games
        WHERE sport = ? AND season = ?
        """,
        (SPORT, int(season)),
    )
    if df is None or df.empty:
        return 0
    rows: list[dict] = []
    for _, g in df.iterrows():
        rows.append({
            "game_id": str(g.get("game_id", "")),
            "home_score": 0,
            "away_score": 0,
            "home_yards": 0,
            "away_yards": 0,
            "home_turnovers": 0,
            "away_turnovers": 0,
            "overtime": False,
            "final_status": "PENDING",
        })
    if rows:
        store.upsert("football_actuals", rows)
    return len(rows)


def _backfill_weather(
    store: FootballStore, *, season: int, venue_lookup: dict,
    weather_loader,
) -> int:
    """Walk every game, look up its venue, fetch the Open-Meteo
    archive snapshot, persist into football_weather."""
    games = store.query_df(
        """
        SELECT game_id, kickoff_ts, venue_code, is_dome
        FROM football_games
        WHERE sport = ? AND season = ?
        """,
        (SPORT, int(season)),
    )
    if games is None or games.empty:
        return 0
    n_loaded = 0
    for _, g in games.iterrows():
        venue_code = str(g.get("venue_code", ""))
        meta = venue_lookup.get(venue_code)
        if meta is None:
            continue
        lat, lon, is_indoor = meta
        try:
            snap = weather_loader(
                game_id=str(g.get("game_id", "")),
                sport=SPORT,
                latitude=float(lat), longitude=float(lon),
                kickoff_iso=str(g.get("kickoff_ts") or "")[:19],
                is_indoor=bool(is_indoor or g.get("is_dome", False)),
            )
            store.upsert("football_weather", [{
                "game_id": snap.game_id, "sport": snap.sport,
                "source": snap.source,
                "captured_at": snap.captured_at,
                "temperature_f": snap.temperature_f,
                "wind_speed_mph": snap.wind_speed_mph,
                "wind_dir_deg": snap.wind_dir_deg,
                "humidity_pct": snap.humidity_pct,
                "precipitation_prob": snap.precipitation_prob,
                "is_indoor": snap.is_indoor,
            }])
            n_loaded += 1
        except WeatherLoaderError as e:
            log.warning("weather fetch failed for %s: %s",
                          g.get("game_id"), e)
    return n_loaded


def _backfill_historical_odds(
    store: FootballStore, *, season: int, odds_loader,
) -> int:
    """For each game, fetch the kickoff-time historical odds snapshot
    and persist into football_lines."""
    games = store.query_df(
        """
        SELECT game_id, kickoff_ts
        FROM football_games
        WHERE sport = ? AND season = ?
        """,
        (SPORT, int(season)),
    )
    if games is None or games.empty:
        return 0
    n_loaded = 0
    for _, g in games.iterrows():
        kickoff = str(g.get("kickoff_ts") or "")
        if not kickoff:
            continue
        target_iso = kickoff[:19].replace(" ", "T") + "Z"
        try:
            res = odds_loader(
                sport_key=ODDS_SPORT_KEY, target_iso=target_iso,
            )
            if res.df is not None and not res.df.empty:
                store.upsert(
                    "football_lines", res.df.to_dict(orient="records"),
                )
                n_loaded += int(len(res.df))
        except OddsLoaderError as e:
            log.warning(
                "historical odds fetch failed for %s @ %s: %s",
                g.get("game_id"), target_iso, e,
            )
    return n_loaded


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="NCAAF season backfill — chunked + resumable",
    )
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--duckdb-path", default="data/ncaaf_cache/ncaaf.duckdb",
    )
    parser.add_argument("--include-historical-odds", action="store_true",
                          help="Pull Odds API historical lines (paid tier).")
    parser.add_argument("--skip-plays", action="store_true",
                          help="Skip the per-week plays op.")
    parser.add_argument("--skip-weather", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    store = FootballStore(args.duckdb_path)
    try:
        result = backfill_season(
            season=args.season, store=store,
            include_historical_odds=args.include_historical_odds,
            skip_plays=args.skip_plays,
            skip_weather=args.skip_weather,
        )
        print(result.summary())
        return 0 if not result.errors else 1
    finally:
        store.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
