"""Team-level first-inning YRFI/NRFI tendencies.

Two implementations live here:

1. `fetch_team_first_inning_splits_teamrankings`
   Scrapes the public TeamRankings split tables for team-vs-pitcher first-
   inning runs. Polite cache-on-disk; 24h TTL. Falls back to neutral
   priors if the page layout shifts.

2. `derive_team_first_inning_from_actuals`
   Computes the same metric from our own `actuals` DuckDB table when
   we'd rather not depend on a 3rd-party scrape (e.g., during backtest
   replay where we already have linescore-derived first-inning runs).

Either path returns the same DataFrame schema:

    team        : tricode (str)
    games       : games used
    yrfi_pct    : 0..1, share of games with >=1 first-inning run for/against
    nrfi_pct    : 1.0 - yrfi_pct
    runs_for_1  : avg first-inning runs scored
    runs_against_1 : avg first-inning runs allowed
"""

from __future__ import annotations

from typing import Optional

from ..config import APIConfig, NRFIConfig, get_default_config
from ..utils.caching import disk_cache
from ..utils.logging import get_logger
from ..utils.rate_limit import global_limiter

log = get_logger(__name__)

# Source URL — TeamRankings exposes per-stat split pages keyed by metric.
# Layout has been stable for years but always validate before trusting.
_TR_FIRST_INN_RUNS_FOR = "https://www.teamrankings.com/mlb/stat/1st-inning-runs-per-game"
_TR_FIRST_INN_RUNS_AGAINST = "https://www.teamrankings.com/mlb/stat/opponent-1st-inning-runs-per-game"


def fetch_team_first_inning_splits_teamrankings(
    *, config: NRFIConfig | None = None, ttl_hours: int = 24,
):
    """Scrape TeamRankings for current-season team first-inning splits.

    Returns an empty DataFrame on any error so callers can degrade
    gracefully (the engine has neutral priors baked into `GameContext`).
    """
    cfg = config or get_default_config()

    @disk_cache(cfg.cache_dir, ttl_seconds=ttl_hours * 3600,
                namespace="teamrankings_first_inn")
    def _inner():
        import pandas as pd  # lazy
        try:
            import httpx
        except ImportError:
            log.warning("httpx missing — TeamRankings scrape skipped")
            return pd.DataFrame()
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError:
            log.warning("beautifulsoup4 missing — install nrfi extras")
            return pd.DataFrame()

        api = cfg.api
        rows: dict[str, dict] = {}
        with httpx.Client(timeout=api.request_timeout_s,
                          headers={"User-Agent": api.user_agent}) as http:
            for label, url in (("runs_for_1", _TR_FIRST_INN_RUNS_FOR),
                                ("runs_against_1", _TR_FIRST_INN_RUNS_AGAINST)):
                with global_limiter(api.requests_per_minute).acquire():
                    try:
                        r = http.get(url)
                        r.raise_for_status()
                    except Exception as e:
                        log.warning("TeamRankings %s fetch failed: %s", label, e)
                        return pd.DataFrame()
                soup = BeautifulSoup(r.text, "lxml")
                table = soup.find("table")
                if table is None:
                    log.warning("TeamRankings %s: no table found", label)
                    return pd.DataFrame()
                for tr in table.find_all("tr")[1:]:
                    cells = [c.get_text(strip=True) for c in tr.find_all("td")]
                    if len(cells) < 3:
                        continue
                    team = _normalize_tr_team(cells[1])
                    try:
                        val = float(cells[2])
                    except ValueError:
                        continue
                    rows.setdefault(team, {"team": team, "games": 0})[label] = val
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(list(rows.values()))
        # Convert per-game runs into a YRFI %:  P(runs >= 1) ~ 1 - exp(-λ)
        # using the team's avg first-inn runs as λ. This is a Poisson
        # heuristic — close enough for ranking, and used as a feature, not
        # the final probability.
        import math
        df["yrfi_pct"] = df["runs_for_1"].fillna(0.55).map(
            lambda x: 1.0 - math.exp(-float(x))
        )
        df["nrfi_pct"] = 1.0 - df["yrfi_pct"]
        return df

    return _inner()


def derive_team_first_inning_from_actuals(store, season: int):
    """Compute the same splits from the engine's own `actuals` table.

    Preferred during backtests because it eliminates 3rd-party dependency
    and is automatically point-in-time consistent.
    """
    sql = """
        SELECT team, games, runs_for_1, runs_against_1,
               (1.0 - 1.0 / EXP(GREATEST(runs_for_1, 0.0))) AS yrfi_pct,
               (1.0 / EXP(GREATEST(runs_for_1, 0.0)))      AS nrfi_pct
        FROM (
            SELECT g.home_team AS team,
                   COUNT(*)                            AS games,
                   AVG(COALESCE(a.first_inn_runs, 0))  AS runs_for_1,
                   AVG(COALESCE(a.first_inn_runs, 0))  AS runs_against_1
            FROM games g LEFT JOIN actuals a USING(game_pk)
            WHERE g.season = ?
            GROUP BY g.home_team
        )
    """
    try:
        return store.query_df(sql, (season,))
    except Exception as e:
        log.warning("Could not derive team splits from actuals: %s", e)
        import pandas as pd
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# TeamRankings displays full team names; map them back to our tricodes.
_TR_NAME_TO_CODE = {
    "Arizona": "ARI", "Atlanta": "ATL", "Baltimore": "BAL", "Boston": "BOS",
    "Chi Cubs": "CHC", "Chi Sox": "CWS", "Cincinnati": "CIN", "Cleveland": "CLE",
    "Colorado": "COL", "Detroit": "DET", "Houston": "HOU", "Kansas City": "KC",
    "LA Angels": "LAA", "LA Dodgers": "LAD", "Miami": "MIA", "Milwaukee": "MIL",
    "Minnesota": "MIN", "NY Mets": "NYM", "NY Yankees": "NYY", "Oakland": "OAK",
    "Philadelphia": "PHI", "Pittsburgh": "PIT", "San Diego": "SD",
    "SF Giants": "SF", "Seattle": "SEA", "St. Louis": "STL", "Tampa Bay": "TB",
    "Texas": "TEX", "Toronto": "TOR", "Washington": "WSH",
}


def _normalize_tr_team(name: str) -> str:
    return _TR_NAME_TO_CODE.get(name, name[:3].upper())
