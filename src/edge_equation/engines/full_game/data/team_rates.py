"""Per-team rolling rates — runs scored / runs allowed.

The full-game projection layer multiplies team offensive output by the
opposing team's pitching weakness (and vice-versa) to estimate expected
runs. This module is the data side of that:

* `TeamRollingRates` — `runs_per_game`, `runs_allowed_per_game`,
  `n_games`, `last_game_date`.
* `bayesian_blend(observed, n, prior, weight)` — same shrinkage helper
  Props uses, so call-up teams (early season, 5 games played) blend
  meaningfully toward the league prior.

The skeleton ships with placeholder load functions; per-team rates
get computed off `fullgame_actuals` once that table has data. For the
day-one projection a `default_team_rates_table()` returns the league
prior for every supported tricode so callers always get a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# League priors — 2025-26 MLB season averages.
# ---------------------------------------------------------------------------


# Average runs per game per team across MLB 2025-26 (~4.6 R/G per team,
# ~9.2 R/G total). Updated annually as part of the offseason refresh.
LEAGUE_RUNS_PER_GAME: float = 4.55
LEAGUE_RUNS_ALLOWED_PER_GAME: float = 4.55  # zero-sum across the league


@dataclass(frozen=True)
class TeamRollingRates:
    """Per-team rolling rates over a `lookback_days` window."""
    team_tricode: str
    n_games: int
    end_date: str
    lookback_days: int
    runs_per_game: float = LEAGUE_RUNS_PER_GAME
    runs_allowed_per_game: float = LEAGUE_RUNS_ALLOWED_PER_GAME

    def offensive_strength(self, league_avg: float = LEAGUE_RUNS_PER_GAME) -> float:
        """Multiplicative offensive strength relative to league average.
        1.10 = team scores 10% more than league avg."""
        if league_avg <= 0:
            return 1.0
        return self.runs_per_game / league_avg

    def pitching_strength(self,
                            league_avg: float = LEAGUE_RUNS_ALLOWED_PER_GAME) -> float:
        """Pitching strength — LOWER runs allowed = HIGHER strength.
        Returns the multiplier you'd apply to the OPPONENT's offensive
        rate to get expected runs against this team. 0.90 = this team's
        pitching suppresses scoring 10% below league average."""
        if league_avg <= 0:
            return 1.0
        return self.runs_allowed_per_game / league_avg


# ---------------------------------------------------------------------------
# Bayesian blend — same shape as Props' helper.
# ---------------------------------------------------------------------------


def bayesian_blend(
    observed_rate: float, n_observed: int,
    prior_rate: float, prior_weight: float,
) -> float:
    """Shrink `observed_rate` toward `prior_rate`."""
    n_obs = max(0, int(n_observed))
    if n_obs == 0:
        return float(prior_rate)
    return (n_obs * observed_rate + prior_weight * prior_rate) / (
        n_obs + prior_weight
    )


# ---------------------------------------------------------------------------
# Default rates table (skeleton — placeholder until live calc lands)
# ---------------------------------------------------------------------------


# Set of supported MLB tricodes. The Athletics relocated mid-2025 and
# the Stats API now returns "ATH" in the schedule payload while older
# rows in fullgame_actuals still carry "OAK" — keep both so neither
# gets silently dropped from the rolling-rate aggregation. Same idea
# for any future relocation: add the new code without removing the
# old, then sunset once historical rows have aged out of the lookback
# window.
_MLB_TRICODES = (
    "ARI", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE", "COL", "DET",
    "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN", "NYM", "NYY", "OAK",
    "PHI", "PIT", "SD", "SF", "SEA", "STL", "TB", "TEX", "TOR", "WSH",
    "ATH",   # 2025+ Athletics (replaces OAK in current schedule payloads)
)


def default_team_rates_table(end_date: str = "",
                                lookback_days: int = 45) -> dict[str, TeamRollingRates]:
    """Return a dict {tricode → TeamRollingRates} populated with the
    league prior for every team. Caller can then override entries with
    real per-team rates as they become available.

    Used as the projection's starting point so the engine produces a
    number for every game on the slate, even when the actuals table
    is empty (early season or fresh DB).
    """
    return {
        tri: TeamRollingRates(
            team_tricode=tri, n_games=0,
            end_date=end_date, lookback_days=lookback_days,
            runs_per_game=LEAGUE_RUNS_PER_GAME,
            runs_allowed_per_game=LEAGUE_RUNS_ALLOWED_PER_GAME,
        )
        for tri in _MLB_TRICODES
    }


def compute_team_rates_from_actuals(
    actuals_df, *, team_tricode: str,
    end_date: str, lookback_days: int = 45,
) -> TeamRollingRates:
    """Aggregate runs_scored / runs_allowed from a `fullgame_actuals`
    DataFrame filtered to the team's recent games. The frame must have
    columns: game_pk, event_date, home_team, away_team, home_runs,
    away_runs.

    Returns a TeamRollingRates with the league prior when the frame is
    empty so callers can always blend without None-checks.
    """
    if actuals_df is None or len(actuals_df) == 0:
        return TeamRollingRates(
            team_tricode=team_tricode, n_games=0,
            end_date=end_date, lookback_days=lookback_days,
        )

    n = 0
    runs_for = 0.0
    runs_allowed = 0.0
    for _, row in actuals_df.iterrows():
        home = str(row.get("home_team") or "")
        away = str(row.get("away_team") or "")
        try:
            hr = float(row.get("home_runs") or 0)
            ar = float(row.get("away_runs") or 0)
        except (TypeError, ValueError):
            continue
        if home == team_tricode:
            runs_for += hr
            runs_allowed += ar
            n += 1
        elif away == team_tricode:
            runs_for += ar
            runs_allowed += hr
            n += 1
    if n == 0:
        return TeamRollingRates(
            team_tricode=team_tricode, n_games=0,
            end_date=end_date, lookback_days=lookback_days,
        )
    return TeamRollingRates(
        team_tricode=team_tricode, n_games=n,
        end_date=end_date, lookback_days=lookback_days,
        runs_per_game=runs_for / n,
        runs_allowed_per_game=runs_allowed / n,
    )


# ---------------------------------------------------------------------------
# DuckDB-backed loader (real per-team rates from fullgame_actuals)
# ---------------------------------------------------------------------------


_TEAM_RATES_QUERY = """
SELECT
    game_pk, event_date, home_team, away_team, home_runs, away_runs
FROM fullgame_actuals
WHERE event_date BETWEEN ? AND ?
  AND home_team IS NOT NULL
  AND away_team IS NOT NULL
  AND home_runs IS NOT NULL
  AND away_runs IS NOT NULL
"""


def load_team_rates_table(
    store, *,
    end_date: str,
    lookback_days: int = 45,
) -> dict[str, TeamRollingRates]:
    """Build the per-tricode rates dict from the engine's own DuckDB.

    Pulls every ``fullgame_actuals`` row in the trailing window and
    aggregates per-team runs scored / runs allowed. Tricodes that
    appear in actuals get real ``n_games > 0`` rates; tricodes that
    don't (early-season teams, teams that haven't played in the
    window) get the league prior (``n_games = 0``) so callers always
    receive a complete table for every supported MLB team.

    The returned dict can be passed straight to
    ``project_all(rates_by_team=...)``. Tricodes with ``n_games == 0``
    yield ``confidence == 0.30`` projections, which the edge module's
    confidence floor (introduced 2026-05-01) skips before publishing.
    """
    import logging
    from datetime import date as _date, timedelta
    log = logging.getLogger(__name__)

    end = _date.fromisoformat(end_date)
    start = end - timedelta(days=int(lookback_days))

    df = None
    try:
        df = store.query_df(_TEAM_RATES_QUERY, (start.isoformat(),
                                                    end.isoformat()))
    except Exception as e:
        # Schema not migrated yet, table empty, anything else — fall
        # through to the league-prior table. The orchestrator's
        # confidence floor still keeps publication honest.
        log.warning("FG team rates query failed (%s): %s",
                      type(e).__name__, e)
        df = None

    table = default_team_rates_table(
        end_date=end_date, lookback_days=lookback_days,
    )
    if df is None or len(df) == 0:
        log.info(
            "FG team rates: 0 rows in %s..%s window — every tricode "
            "falls back to league prior.",
            start.isoformat(), end.isoformat(),
        )
        return table

    # Diagnostic: log distinct tricodes seen in actuals so we can spot
    # tricode drift (e.g. Athletics ATH vs OAK relocation rename) at
    # first sight in the workflow log instead of silently dropping
    # those teams from the rolling-rate aggregation.
    try:
        seen = set()
        for col in ("home_team", "away_team"):
            if col in df.columns:
                seen.update(str(x) for x in df[col].dropna().unique())
        seen.discard("")
        unknown = sorted(seen - set(table.keys()))
        if unknown:
            log.warning(
                "FG team rates: %d tricodes in actuals not recognized: %s "
                "(consider adding to _MLB_TRICODES).",
                len(unknown), unknown,
            )
        log.info(
            "FG team rates: %d rows in window, %d distinct tricodes seen.",
            len(df), len(seen),
        )
    except Exception:
        pass

    for tri in list(table.keys()):
        rates = compute_team_rates_from_actuals(
            df, team_tricode=tri, end_date=end_date,
            lookback_days=lookback_days,
        )
        if rates.n_games > 0:
            table[tri] = rates
    return table
