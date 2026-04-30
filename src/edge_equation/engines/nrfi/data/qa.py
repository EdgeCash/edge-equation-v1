"""NRFI corpus quality audit.

This module answers the first question we should ask before every training run:
"Is the historical corpus complete enough to trust?"  It is intentionally
read-only and DuckDB-friendly so it can run after each resumable backfill chunk.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..config import get_default_config
from .storage import NRFIStore


@dataclass(frozen=True)
class CorpusQASummary:
    """High-level counts and missing-data rates for the NRFI corpus."""

    games: int
    actuals: int
    features: int
    trainable_rows: int
    min_feature_date: Optional[str] = None
    max_feature_date: Optional[str] = None
    missing_home_pitcher_pct: float = 0.0
    missing_away_pitcher_pct: float = 0.0
    missing_lineup_pct: float = 0.0
    missing_umpire_pct: float = 0.0
    missing_weather_pct: float = 0.0
    nrfi_rate: Optional[float] = None
    by_month: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "NRFI corpus QA",
            "-" * 56,
            f"  games                {self.games}",
            f"  actuals              {self.actuals}",
            f"  features             {self.features}",
            f"  trainable rows       {self.trainable_rows}",
            f"  feature window       {self.min_feature_date or '-'}..{self.max_feature_date or '-'}",
            f"  missing home pitcher {self.missing_home_pitcher_pct:5.1f}%",
            f"  missing away pitcher {self.missing_away_pitcher_pct:5.1f}%",
            f"  missing lineups      {self.missing_lineup_pct:5.1f}%",
            f"  missing umpire       {self.missing_umpire_pct:5.1f}%",
            f"  missing weather      {self.missing_weather_pct:5.1f}%",
        ]
        if self.nrfi_rate is not None:
            lines.append(f"  actual NRFI rate     {self.nrfi_rate * 100.0:5.1f}%")
        if self.by_month:
            lines.extend(["", "By month"])
            lines.append("  month     games  features  actuals  trainable")
            for row in self.by_month:
                lines.append(
                    f"  {row['month']}  {row['games']:>5}  {row['features']:>8}  "
                    f"{row['actuals']:>7}  {row['trainable']:>9}"
                )
        return "\n".join(lines)


def build_corpus_qa(store: NRFIStore) -> CorpusQASummary:
    """Compute corpus QA from the current DuckDB store."""

    games = _count(store, "games")
    actuals = _count(store, "actuals")
    features = _count(store, "features")

    trainable = store.query_df(
        """
        SELECT COUNT(*) AS n,
               MIN(g.game_date) AS min_date,
               MAX(g.game_date) AS max_date,
               AVG(CASE WHEN a.nrfi THEN 1.0 ELSE 0.0 END) AS nrfi_rate
        FROM features f
        JOIN actuals a USING(game_pk)
        JOIN games g USING(game_pk)
        """
    )
    trainable_rows = int(trainable.iloc[0]["n"] or 0)
    min_feature_date = _date_str(trainable.iloc[0]["min_date"])
    max_feature_date = _date_str(trainable.iloc[0]["max_date"])
    nrfi_rate = trainable.iloc[0]["nrfi_rate"]

    missing = store.query_df(
        """
        SELECT
          AVG(CASE WHEN home_pitcher_id IS NULL OR home_pitcher_id = 0 THEN 1.0 ELSE 0.0 END) AS miss_home_p,
          AVG(CASE WHEN away_pitcher_id IS NULL OR away_pitcher_id = 0 THEN 1.0 ELSE 0.0 END) AS miss_away_p,
          AVG(CASE WHEN home_lineup IS NULL OR home_lineup = '' OR away_lineup IS NULL OR away_lineup = '' THEN 1.0 ELSE 0.0 END) AS miss_lineup,
          AVG(CASE WHEN ump_id IS NULL OR ump_id = 0 THEN 1.0 ELSE 0.0 END) AS miss_ump
        FROM games
        """
    )

    weather = store.query_df(
        """
        SELECT
          CASE WHEN (SELECT COUNT(*) FROM games) = 0 THEN 0.0
               ELSE 1.0 - (SELECT COUNT(*) FROM weather) * 1.0 / (SELECT COUNT(*) FROM games)
          END AS miss_weather
        """
    )

    by_month_df = store.query_df(
        """
        WITH months AS (
          SELECT strftime(game_date, '%Y-%m') AS month, COUNT(*) AS games
          FROM games GROUP BY 1
        ),
        feats AS (
          SELECT strftime(g.game_date, '%Y-%m') AS month, COUNT(*) AS features
          FROM features f JOIN games g USING(game_pk) GROUP BY 1
        ),
        acts AS (
          SELECT strftime(g.game_date, '%Y-%m') AS month, COUNT(*) AS actuals
          FROM actuals a JOIN games g USING(game_pk) GROUP BY 1
        ),
        train AS (
          SELECT strftime(g.game_date, '%Y-%m') AS month, COUNT(*) AS trainable
          FROM features f
          JOIN actuals a USING(game_pk)
          JOIN games g USING(game_pk)
          GROUP BY 1
        )
        SELECT m.month,
               m.games,
               COALESCE(f.features, 0) AS features,
               COALESCE(a.actuals, 0) AS actuals,
               COALESCE(t.trainable, 0) AS trainable
        FROM months m
        LEFT JOIN feats f USING(month)
        LEFT JOIN acts a USING(month)
        LEFT JOIN train t USING(month)
        ORDER BY m.month
        """
    )

    return CorpusQASummary(
        games=games,
        actuals=actuals,
        features=features,
        trainable_rows=trainable_rows,
        min_feature_date=min_feature_date,
        max_feature_date=max_feature_date,
        missing_home_pitcher_pct=_pct(missing.iloc[0]["miss_home_p"]),
        missing_away_pitcher_pct=_pct(missing.iloc[0]["miss_away_p"]),
        missing_lineup_pct=_pct(missing.iloc[0]["miss_lineup"]),
        missing_umpire_pct=_pct(missing.iloc[0]["miss_ump"]),
        missing_weather_pct=_pct(weather.iloc[0]["miss_weather"]),
        nrfi_rate=float(nrfi_rate) if nrfi_rate == nrfi_rate else None,
        by_month=[
            {
                "month": str(row.month),
                "games": int(row.games),
                "features": int(row.features),
                "actuals": int(row.actuals),
                "trainable": int(row.trainable),
            }
            for _, row in by_month_df.iterrows()
        ],
    )


def _count(store: NRFIStore, table: str) -> int:
    df = store.query_df(f"SELECT COUNT(*) AS n FROM {table}")
    return int(df.iloc[0]["n"])


def _pct(value) -> float:
    try:
        if value != value:
            return 0.0
        return float(value) * 100.0
    except Exception:
        return 0.0


def _date_str(value) -> Optional[str]:
    if value is None or value != value:
        return None
    return str(value)[:10]


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit NRFI corpus quality.")
    parser.parse_args(list(argv) if argv is not None else None)
    cfg = get_default_config().resolve_paths()
    store = NRFIStore(cfg.duckdb_path)
    print(build_corpus_qa(store).summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())

