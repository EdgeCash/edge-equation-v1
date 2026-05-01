"""Full-Game ETL — pull MLB Stats API linescores into ``fullgame_actuals``.

Mirrors the NRFI engine's ``backfill_actuals`` pattern, but persists
final + first-5-inning team scores plus the team tricodes so the
team-rates loader can compute per-team rolling rates without a
cross-engine join.

Source: the same MLB Stats API ``schedule`` + ``linescore`` endpoints
NRFI uses, accessed via ``MLBStatsClient`` (re-imported from
``nrfi.data.scrapers_etl`` so we don't duplicate the HTTP client).

Usage
~~~~~

    >>> from edge_equation.engines.full_game.data.storage import FullGameStore
    >>> store = FullGameStore("data/fullgame_cache/fullgame.duckdb")
    >>> from edge_equation.engines.full_game.data.scrapers_etl import (
    ...     backfill_fullgame_actuals,
    ... )
    >>> n = backfill_fullgame_actuals("2026-04-01", "2026-04-30", store)

Idempotent: rows are upserted on ``game_pk`` so repeated runs over
the same window are safe (and almost free — DuckDB writes are fast).

Best-effort throughout — a single linescore fetch failure just skips
that game; the pass continues for the rest of the date range.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Linescore parsing
# ---------------------------------------------------------------------------


def full_game_runs(linescore: dict) -> tuple[Optional[int], Optional[int]]:
    """Return ``(home_runs, away_runs)`` from a Stats API linescore.

    Sums ``runs`` across every inning for both sides. Returns
    ``(None, None)`` when the linescore is empty (game scheduled but
    not yet played, or rained out).
    """
    innings = linescore.get("innings") or []
    if not innings:
        return None, None
    home_total = 0
    away_total = 0
    for inning in innings:
        home_total += int(((inning.get("home") or {}).get("runs", 0)) or 0)
        away_total += int(((inning.get("away") or {}).get("runs", 0)) or 0)
    return home_total, away_total


def first_five_innings_runs(
    linescore: dict,
) -> tuple[Optional[int], Optional[int]]:
    """Return ``(f5_home_runs, f5_away_runs)`` — runs scored in
    innings 1-5. Returns ``(None, None)`` when fewer than 5 innings
    have been played (game in progress or short game)."""
    innings = linescore.get("innings") or []
    if len(innings) < 5:
        # Don't extrapolate — F5 totals require all 5 innings on record.
        return None, None
    home_f5 = 0
    away_f5 = 0
    for inning in innings:
        num = inning.get("num")
        if num is None or int(num) > 5:
            continue
        home_f5 += int(((inning.get("home") or {}).get("runs", 0)) or 0)
        away_f5 += int(((inning.get("away") or {}).get("runs", 0)) or 0)
    return home_f5, away_f5


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def backfill_fullgame_actuals(
    start_date: str,
    end_date: str,
    store,
    *,
    config=None,
    client=None,
) -> int:
    """Walk the schedule day-by-day and upsert ``fullgame_actuals`` rows.

    Pulls schedule stubs (which carry ``game_pk`` + tricode columns)
    for each day in ``[start_date, end_date]``, then fetches each
    game's linescore to extract final + F5 totals. Persists every
    completed game; in-progress / postponed games are skipped silently
    (they'll show up in tomorrow's pass).

    Returns the number of rows upserted across the entire window.
    """
    # Local import to avoid a circular dep at module load time and to
    # keep the FG engine optional (the import only fires when this
    # function is called, which only happens in the workflow run).
    from edge_equation.engines.nrfi.data.scrapers_etl import MLBStatsClient
    from edge_equation.engines.nrfi.config import (
        NRFIConfig, get_default_config as _nrfi_default_config,
    )

    # Build a stripped client config — we only need the API timeouts.
    nrfi_cfg = config if isinstance(config, NRFIConfig) else _nrfi_default_config()
    owns_client = client is None
    client = client or MLBStatsClient(nrfi_cfg.api)

    n = 0
    try:
        d0 = date.fromisoformat(start_date)
        d1 = date.fromisoformat(end_date)
        cur = d0
        while cur <= d1:
            try:
                stubs = client.schedule(cur.isoformat())
            except Exception as e:
                log.warning(
                    "FG backfill: schedule fetch failed for %s (%s): %s",
                    cur.isoformat(), type(e).__name__, e,
                )
                cur = cur + timedelta(days=1)
                continue

            rows: list[dict] = []
            for s in stubs:
                try:
                    ls = client.linescore(s.game_pk)
                except Exception as e:
                    log.warning(
                        "FG backfill: linescore fetch failed for "
                        "game_pk=%s on %s (%s): %s",
                        s.game_pk, cur.isoformat(), type(e).__name__, e,
                    )
                    continue
                home_runs, away_runs = full_game_runs(ls)
                f5_home, f5_away = first_five_innings_runs(ls)
                if home_runs is None or away_runs is None:
                    # Game hasn't been played yet (or no innings on record).
                    continue
                rows.append({
                    "game_pk":      int(s.game_pk),
                    "event_date":   cur.isoformat(),
                    "home_team":    str(s.home_team),
                    "away_team":    str(s.away_team),
                    "home_runs":    int(home_runs),
                    "away_runs":    int(away_runs),
                    "f5_home_runs": (
                        int(f5_home) if f5_home is not None else None
                    ),
                    "f5_away_runs": (
                        int(f5_away) if f5_away is not None else None
                    ),
                })

            if rows:
                store.upsert("fullgame_actuals", rows)
                n += len(rows)

            cur = cur + timedelta(days=1)
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:
                pass

    log.info("FG backfill: %d games persisted to fullgame_actuals.", n)

    # Read-back diagnostic — confirms what actually landed in the DB.
    # Run #28 reported "816 games persisted" but the team-rates query
    # found 0 rows in the lookback window, suggesting either the rows
    # never made it (silent upsert failure), or they have NULL
    # home_team/away_team that the query's IS NOT NULL filter rejects.
    # This SELECT-after-write tells us definitively.
    try:
        df_check = store.query_df(
            """
            SELECT COUNT(*) AS n_total,
                   COUNT(home_team) AS n_with_home_team,
                   COUNT(home_runs) AS n_with_home_runs,
                   MIN(event_date) AS min_date,
                   MAX(event_date) AS max_date,
                   COUNT(DISTINCT home_team) AS n_distinct_home_teams
            FROM fullgame_actuals
            """
        )
        if df_check is not None and len(df_check) > 0:
            row = df_check.iloc[0]
            log.info(
                "FG backfill diagnostic: total=%s with_home_team=%s "
                "with_home_runs=%s date_range=%s..%s distinct_home_teams=%s",
                row.get("n_total"), row.get("n_with_home_team"),
                row.get("n_with_home_runs"),
                row.get("min_date"), row.get("max_date"),
                row.get("n_distinct_home_teams"),
            )
        # Sample first row to see actual values in the columns.
        df_sample = store.query_df(
            "SELECT * FROM fullgame_actuals ORDER BY event_date DESC LIMIT 3"
        )
        if df_sample is not None and len(df_sample) > 0:
            for i, r in df_sample.iterrows():
                log.info("FG backfill sample row: %s", dict(r))
    except Exception as e:
        log.warning("FG backfill diagnostic SELECT failed: %s: %s",
                      type(e).__name__, e)

    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Backfill fullgame_actuals from MLB Stats API.",
    )
    parser.add_argument("--duckdb-path", required=True)
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    from .storage import FullGameStore
    store = FullGameStore(args.duckdb_path)
    try:
        n = backfill_fullgame_actuals(
            args.start_date, args.end_date, store,
        )
    finally:
        store.close()
    print(f"FG backfill: {n} games persisted.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
