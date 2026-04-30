"""Football corpus diagnostics CLI.

Reports on the size + health of a football DuckDB after a backfill
run, so operators can answer:

* "Did the season actually load?"
* "Which feature columns are sparse / missing?"
* "What does a sample row look like?"

Usage
~~~~~

::

    python -m edge_equation.engines.football_core.data.diagnostics \\
        --duckdb-path data/nfl_cache/nfl.duckdb --sport NFL

    python -m edge_equation.engines.football_core.data.diagnostics \\
        --duckdb-path data/ncaaf_cache/ncaaf.duckdb --sport NCAAF \\
        --season 2025

The output is plain text suited for piping into a status report. We
intentionally don't emit JSON — the audience is the operator looking
at the corpus by eye, not a downstream pipeline.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .storage import FootballStore


@dataclass
class CorpusReport:
    """Roll-up of one diagnostics run."""
    sport: str
    season: Optional[int] = None
    n_games: int = 0
    n_plays: int = 0
    n_actuals: int = 0
    n_props: int = 0
    n_lines: int = 0
    n_weather: int = 0
    n_features: int = 0
    n_completed_ops: int = 0
    n_failed_ops: int = 0
    games_missing_kickoff_pct: float = 0.0
    games_missing_venue_pct: float = 0.0
    weather_coverage_pct: float = 0.0
    sample_game: dict = field(default_factory=dict)
    failed_ops: list[tuple[str, str, str]] = field(default_factory=list)

    def render(self) -> str:
        lines: list[str] = []
        scope = f"{self.sport}"
        if self.season is not None:
            scope += f" — season {self.season}"
        lines.append(f"Football corpus diagnostics — {scope}")
        lines.append("=" * 50)
        lines.append("")
        lines.append("Row counts")
        lines.append("─" * 50)
        lines.append(f"  games          {self.n_games:>8}")
        lines.append(f"  plays          {self.n_plays:>8}")
        lines.append(f"  actuals        {self.n_actuals:>8}")
        lines.append(f"  props          {self.n_props:>8}")
        lines.append(f"  lines          {self.n_lines:>8}")
        lines.append(f"  weather        {self.n_weather:>8}")
        lines.append(f"  features       {self.n_features:>8}")
        lines.append("")
        lines.append("Coverage")
        lines.append("─" * 50)
        lines.append(
            f"  games w/ kickoff   "
            f"{100.0 - self.games_missing_kickoff_pct:>5.1f}%"
        )
        lines.append(
            f"  games w/ venue     "
            f"{100.0 - self.games_missing_venue_pct:>5.1f}%"
        )
        lines.append(
            f"  weather coverage   {self.weather_coverage_pct:>5.1f}%"
        )
        lines.append("")
        lines.append("Backfill checkpoints")
        lines.append("─" * 50)
        lines.append(f"  completed ops      {self.n_completed_ops}")
        lines.append(f"  failed ops         {self.n_failed_ops}")
        if self.failed_ops:
            lines.append("  recent failures:")
            for tgt, op, err in self.failed_ops[:5]:
                lines.append(f"    [{tgt} {op}] {err[:80]}")
        lines.append("")
        if self.sample_game:
            lines.append("Sample game")
            lines.append("─" * 50)
            for k, v in self.sample_game.items():
                lines.append(f"  {k:<18} {v}")
            lines.append("")
        return "\n".join(lines)


def run_diagnostics(
    store: FootballStore, *,
    sport: str,
    season: Optional[int] = None,
) -> CorpusReport:
    """Build a `CorpusReport` from the DuckDB at `store`."""
    report = CorpusReport(sport=sport, season=season)

    # Row counts — scope to (sport[, season]) where applicable.
    report.n_games = _count(
        store, "football_games", sport=sport, season=season,
    )
    report.n_plays = _count(
        store, "football_plays", sport=sport,
    )
    report.n_actuals = _count_join_games(
        store, "football_actuals", sport=sport, season=season,
    )
    report.n_props = _count_join_games(
        store, "football_props", sport=sport, season=season,
    )
    report.n_lines = _count_join_games(
        store, "football_lines", sport=sport, season=season,
    )
    report.n_weather = _count(
        store, "football_weather", sport=sport,
    )
    report.n_features = _count(
        store, "football_features", sport=sport,
    )

    # Coverage rates.
    report.games_missing_kickoff_pct = _pct_missing(
        store, "football_games", "kickoff_ts",
        sport=sport, season=season,
    )
    report.games_missing_venue_pct = _pct_missing(
        store, "football_games", "venue_code",
        sport=sport, season=season,
    )
    if report.n_games > 0:
        report.weather_coverage_pct = round(
            100.0 * report.n_weather / report.n_games, 1,
        )

    # Checkpoint state.
    completed = store.query_df(
        """
        SELECT COUNT(*) AS n
        FROM football_backfill_checkpoints
        WHERE sport = ? AND error IS NULL
        """,
        (sport,),
    )
    failed = store.query_df(
        """
        SELECT target_date, op, COALESCE(error, '') AS error
        FROM football_backfill_checkpoints
        WHERE sport = ? AND error IS NOT NULL
        ORDER BY completed_at DESC
        LIMIT 20
        """,
        (sport,),
    )
    report.n_completed_ops = (
        int(completed.iloc[0]["n"]) if completed is not None
        and not completed.empty else 0
    )
    if failed is not None and not failed.empty:
        report.n_failed_ops = int(len(failed))
        report.failed_ops = [
            (str(r.target_date), str(r.op), str(r.error))
            for _, r in failed.iterrows()
        ]

    # Sample game (most recent kickoff).
    sample = store.query_df(
        """
        SELECT game_id, season, week, event_date, kickoff_ts,
               home_team, away_team, venue, is_dome
        FROM football_games
        WHERE sport = ?
        """ + (" AND season = ?" if season is not None else "") + """
        ORDER BY kickoff_ts DESC NULLS LAST
        LIMIT 1
        """,
        (sport, int(season)) if season is not None else (sport,),
    )
    if sample is not None and not sample.empty:
        row = sample.iloc[0]
        report.sample_game = {
            col: str(row[col]) for col in sample.columns
        }

    return report


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _count(
    store: FootballStore, table: str, *,
    sport: str, season: Optional[int] = None,
) -> int:
    sql = f"SELECT COUNT(*) AS n FROM {table} WHERE sport = ?"
    params: tuple = (sport,)
    if season is not None and table == "football_games":
        sql += " AND season = ?"
        params = (sport, int(season))
    df = store.query_df(sql, params)
    if df is None or df.empty:
        return 0
    return int(df.iloc[0]["n"])


def _count_join_games(
    store: FootballStore, table: str, *,
    sport: str, season: Optional[int] = None,
) -> int:
    """Count rows in `table` joined to football_games for sport/season filters.

    Several tables (actuals, props, lines) don't carry a sport
    discriminator directly — they're scoped via game_id.
    """
    if season is None:
        sql = (
            f"SELECT COUNT(*) AS n FROM {table} t "
            f"JOIN football_games g ON t.game_id = g.game_id "
            f"WHERE g.sport = ?"
        )
        params: tuple = (sport,)
    else:
        sql = (
            f"SELECT COUNT(*) AS n FROM {table} t "
            f"JOIN football_games g ON t.game_id = g.game_id "
            f"WHERE g.sport = ? AND g.season = ?"
        )
        params = (sport, int(season))
    df = store.query_df(sql, params)
    if df is None or df.empty:
        return 0
    return int(df.iloc[0]["n"])


def _pct_missing(
    store: FootballStore, table: str, column: str, *,
    sport: str, season: Optional[int] = None,
) -> float:
    """Return the % of rows where `column` IS NULL or empty string."""
    if season is None:
        sql = (
            f"SELECT "
            f"  SUM(CASE WHEN {column} IS NULL OR CAST({column} AS VARCHAR) = '' "
            f"           THEN 1 ELSE 0 END) AS missing, "
            f"  COUNT(*) AS total "
            f"FROM {table} WHERE sport = ?"
        )
        params: tuple = (sport,)
    else:
        sql = (
            f"SELECT "
            f"  SUM(CASE WHEN {column} IS NULL OR CAST({column} AS VARCHAR) = '' "
            f"           THEN 1 ELSE 0 END) AS missing, "
            f"  COUNT(*) AS total "
            f"FROM {table} WHERE sport = ? AND season = ?"
        )
        params = (sport, int(season))
    df = store.query_df(sql, params)
    if df is None or df.empty:
        return 0.0
    total = int(df.iloc[0]["total"] or 0)
    if total == 0:
        return 0.0
    missing = int(df.iloc[0]["missing"] or 0)
    return round(100.0 * missing / total, 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Football corpus diagnostics — size + missing-rate report",
    )
    parser.add_argument("--duckdb-path", required=True)
    parser.add_argument("--sport", choices=["NFL", "NCAAF"], required=True)
    parser.add_argument("--season", type=int, default=None,
                          help="Optional season filter (default: all loaded).")
    args = parser.parse_args(list(argv) if argv is not None else None)

    store = FootballStore(args.duckdb_path)
    try:
        report = run_diagnostics(store, sport=args.sport, season=args.season)
        print(report.render())
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
