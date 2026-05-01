"""
Ingestion source factory.

Chooses the right ingestion source for a league on a given run date:

1. If a CSV file exists at {csv_dir}/{league}_{YYYY-MM-DD}.csv, use
   ManualCsvSource (caller-authored slate -- highest priority).
2. Else if THE_ODDS_API_KEY is set AND the league maps to a known Odds-API
   sport_key, use TheOddsApiSource (cache-first, costs credits).
3. Else fall back to the hard-coded mock source for development / testing.

The factory never fetches data -- it just returns an object exposing
get_raw_games(run_datetime) and get_raw_markets(run_datetime). The caller
drives the ingest.
"""
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from edge_equation.ingestion.manual_csv_source import ManualCsvSource
from edge_equation.engines.core.data.odds_api_client import API_KEY_ENV_VAR
from edge_equation.ingestion.odds_api_source import TheOddsApiSource
from edge_equation.ingestion.mlb_source import MlbLikeSource
from edge_equation.ingestion.nba_source import NbaSource
from edge_equation.ingestion.nfl_source import NflSource
from edge_equation.ingestion.nhl_source import NhlSource
from edge_equation.ingestion.soccer_source import SoccerSource


# league -> Odds-API sport_key. Leagues not in this map fall straight through
# to the mock source; KBO and NPB live there (the free tier doesn't cover them).
# NCAAB / NCAAF are supported by the API but lack a local mock source -- add
# one before enabling them here so the factory can always degrade gracefully.
# Must match the sport_key set that the Data Refresher actually
# fetches for (data_fetcher.ODDS_API_SPORT_KEY). If an entry is
# missing here, cadence for that league silently falls back to mock
# sources (or raises for leagues without a mock), so the premium
# email shows 0 overseas picks even when the refresher has real KBO /
# NPB / soccer data sitting in the cache.
LEAGUE_TO_ODDS_API_SPORT_KEY = {
    "MLB": "baseball_mlb",
    "NFL": "americanfootball_nfl",
    "NHL": "icehockey_nhl",
    "NBA": "basketball_nba",
    "KBO": "baseball_kbo",
    "NPB": "baseball_npb",
    "EPL": "soccer_epl",
    "UCL": "soccer_uefa_champs_league",
}


DEFAULT_CSV_DIR = "data"


def _mock_source_for_league(league: str):
    """Return the stubbed source matching this league, or None if unknown."""
    if league in ("MLB", "KBO", "NPB"):
        return MlbLikeSource(league=league)
    if league == "NBA":
        return NbaSource()
    if league == "NFL":
        return NflSource()
    if league == "NHL":
        return NhlSource()
    if league == "SOC":
        return SoccerSource()
    return None


def nrfi_source_for_league(league: str):
    """Return the elite NRFI/YRFI source for `league`, or None if the
    `[nrfi]` extras aren't installed. Designed as an *additive layer*
    on top of the standard source — callers should concatenate
    `standard_source.get_raw_markets(...)` with `nrfi_source.get_raw_markets(...)`
    and let the dedup-by-(game_id, market_type, selection) rule pick
    the higher-edge winner.

    Only MLB is supported in v1 — KBO/NPB don't have the Statcast
    coverage to feed the elite engine yet.
    """
    if league != "MLB":
        return None
    try:
        from edge_equation.ingestion.mlb_nrfi_source import MLBNRFISource
        return MLBNRFISource()
    except ImportError:
        return None


class SourceFactory:
    """
    Source resolution for scheduled runs:
    - for_league(league, run_date, conn, csv_dir=None, api_key=None) -> source
    - csv_path_for(league, run_date, csv_dir)                        -> Path
    - odds_api_key_set(api_key=None)                                 -> bool
    """

    @staticmethod
    def csv_path_for(league: str, run_date: date, csv_dir: Optional[str] = None) -> Path:
        directory = csv_dir or DEFAULT_CSV_DIR
        return Path(directory) / f"{league.lower()}_{run_date.isoformat()}.csv"

    @staticmethod
    def odds_api_key_set(api_key: Optional[str] = None) -> bool:
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
        return bool(key)

    @staticmethod
    def for_league(
        league: str,
        run_date: date,
        conn=None,
        csv_dir: Optional[str] = None,
        api_key: Optional[str] = None,
        prefer_mock: bool = False,
        cached_only: bool = False,
    ):
        """
        Resolve an ingestion source:
        - Highest priority: dated CSV file on disk.
        - Next:             The Odds API (needs conn + api_key/env var).
        - Fallback:         the mock source for the league.
        Raises ValueError if the league has no mock source and neither CSV nor
        API is available.

        cached_only=True flips the Odds API source into cache-only mode:
        a cache miss returns an empty slate rather than burning a free-
        tier credit. Used by the five cadence workflows so the data-
        refresher job is the only live-API consumer.
        """
        csv_path = SourceFactory.csv_path_for(league, run_date, csv_dir)
        if csv_path.exists():
            return ManualCsvSource(str(csv_path))

        sport_key = LEAGUE_TO_ODDS_API_SPORT_KEY.get(league)
        if (
            not prefer_mock
            and sport_key is not None
            and conn is not None
            and SourceFactory.odds_api_key_set(api_key)
        ):
            return TheOddsApiSource(
                conn=conn,
                sport_key=sport_key,
                api_key=api_key,
                cached_only=cached_only,
            )

        mock = _mock_source_for_league(league)
        if mock is None:
            raise ValueError(
                f"No ingestion source for league {league!r}. "
                f"Provide a CSV at {csv_path} or set {API_KEY_ENV_VAR}."
            )
        return mock
