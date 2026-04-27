"""Historical replay / backtest harness.

The goal is "as-known 2-4hr pre-game" feature reconstruction so the
evaluation isn't poisoned by post-game information. Concretely:

* Pitcher form windows are computed from games strictly BEFORE the
  target game date (no same-day leakage).
* Lineups are taken from the boxscore (in retro mode) since that's the
  only authoritative public source — a small leak vs. true projected
  lineups, but acceptable. To remove it entirely, swap the lineup
  fetcher for a confirmed-only feed and accept higher missing-data rates.
* Weather is pulled from Open-Meteo's archive endpoint (real
  observations) — close to but not identical to the forecast we'd see
  pre-game. This is the right move for fair calibration measurement.
* Umpire / ABS metrics use the running average up to (but not including)
  the target date.

Usage from the CLI:

    from nrfi.evaluation.backtest import backtest_range
    report = backtest_range("2024-04-01", "2024-04-30")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from ..config import NRFIConfig, get_default_config
from ..data.park_factors import park_for
from ..data.scrapers_etl import (
    MLBStatsClient, backfill_actuals, daily_etl, fetch_statcast_first_inning,
    first_inning_runs,
)
from ..data.storage import NRFIStore
from ..data.weather import WeatherClient
from ..features.feature_engineering import (
    FeatureBuilder, GameContext, LineupInputs, PitcherInputs, UmpireInputs,
    features_to_blob,
)
from ..features.splits import first_inning_pitcher_stats
from ..models.inference import NRFIInferenceEngine
from ..models.model_training import TrainedBundle
from ..utils.logging import get_logger
from .metrics import RoiReport, brier_score, log_loss_score, reliability_buckets, simulated_roi

log = get_logger(__name__)


@dataclass
class BacktestReport:
    n_games: int
    brier: float
    log_loss: float
    accuracy: float
    base_rate: float
    reliability: dict
    roi_flat: Optional[RoiReport] = None
    per_game: pd.DataFrame = field(default_factory=pd.DataFrame)


def _date_iter(start: str, end: str) -> Iterable[date]:
    cur = date.fromisoformat(start); end_d = date.fromisoformat(end)
    while cur <= end_d:
        yield cur
        cur += timedelta(days=1)


def reconstruct_features_for_date(
    target_date: str,
    *,
    store: NRFIStore,
    statcast_window_days: int = 30,
    config: NRFIConfig | None = None,
) -> list[tuple[int, dict]]:
    """Build features for every game on `target_date` using only
    information available before first pitch.

    Returns a list of (game_pk, feature_dict) tuples.
    """
    cfg = config or get_default_config()
    builder = FeatureBuilder(cfg)
    weather = WeatherClient(cfg.api)
    mlb = MLBStatsClient(cfg.api)

    # Pull / hydrate the day's schedule (lineups + umps).
    daily_etl(target_date, store, config=cfg)
    games = store.games_for_date(target_date)

    # Statcast first-inning frame for the trailing window.
    end = date.fromisoformat(target_date) - timedelta(days=1)
    start = end - timedelta(days=statcast_window_days)
    statcast_df = fetch_statcast_first_inning(
        start.isoformat(), end.isoformat(), config=cfg,
    )

    out: list[tuple[int, dict]] = []
    try:
        for _, g in games.iterrows():
            try:
                park = park_for(str(g.venue_code))
            except KeyError:
                log.warning("Unknown park %s — skipping game %s", g.venue_code, g.game_pk)
                continue

            # Weather — archive endpoint (matches what we'd see backfilling).
            wx = None
            if g.first_pitch_ts:
                wx = weather.archive(park.lat, park.lon, str(g.first_pitch_ts), park.altitude_ft)

            # Pitcher inputs (first-inning splits from Statcast).
            home_p = PitcherInputs(
                pitcher_id=int(g.home_pitcher_id or 0),
                hand=str(g.home_pitcher_hand or "R"),
                first_inn_stats=first_inning_pitcher_stats(
                    statcast_df, int(g.home_pitcher_id or 0)
                ) if statcast_df is not None and not statcast_df.empty else {},
            )
            away_p = PitcherInputs(
                pitcher_id=int(g.away_pitcher_id or 0),
                hand=str(g.away_pitcher_hand or "R"),
                first_inn_stats=first_inning_pitcher_stats(
                    statcast_df, int(g.away_pitcher_id or 0)
                ) if statcast_df is not None and not statcast_df.empty else {},
            )

            # Lineup inputs — defaults are league averages; richer lookups
            # would join with batter season stats from pybaseball.
            home_lu = LineupInputs(confirmed=bool(g.home_lineup))
            away_lu = LineupInputs(confirmed=bool(g.away_lineup))

            ump = UmpireInputs(
                ump_id=int(g.ump_id) if g.ump_id else None,
                full_name="",  # pulled from `umpires` table when present
            )

            ctx = GameContext(
                game_pk=int(g.game_pk),
                game_date=str(g.game_date),
                season=int(g.season),
                home_team=str(g.home_team),
                away_team=str(g.away_team),
                park=park,
                weather=wx,
                roof_open=None,
            )
            feats = builder.build(
                ctx=ctx,
                home_pitcher=home_p, away_pitcher=away_p,
                home_lineup=home_lu, away_lineup=away_lu,
                umpire=ump,
            )
            out.append((int(g.game_pk), feats))
            store.upsert("features", [{
                "game_pk": int(g.game_pk),
                "model_version": "elite_nrfi_v1",
                "feature_blob": features_to_blob(feats),
            }])
    finally:
        weather.close()
        mlb.close()
    return out


def backtest_range(
    start_date: str, end_date: str,
    *,
    config: NRFIConfig | None = None,
    bundle: Optional[TrainedBundle] = None,
    market_provider=None,
    save_dir: Optional[str | Path] = None,
) -> BacktestReport:
    """Replay every game in [start_date, end_date], score predictions vs actuals.

    Parameters
    ----------
    bundle : Optional pre-trained model bundle. When None, falls back to
        the deterministic Poisson baseline (ML head not used) so the
        backtest can be run before training.
    market_provider : Callable (game_pk) -> Optional[float] returning
        the market-implied NRFI probability. Used for ROI simulation.
    save_dir : Optional directory to drop CSV + plots into.
    """
    cfg = (config or get_default_config()).resolve_paths()
    store = NRFIStore(cfg.duckdb_path)

    inference = None
    if bundle is not None:
        inference = NRFIInferenceEngine(bundle, cfg)

    rows: list[dict] = []
    for d in _date_iter(start_date, end_date):
        try:
            feats_per_game = reconstruct_features_for_date(d.isoformat(),
                                                            store=store, config=cfg)
        except Exception as e:
            log.exception("Feature reconstruction failed for %s: %s", d, e)
            continue
        if not feats_per_game:
            continue
        # Backfill actuals so we have ground truth.
        backfill_actuals(d.isoformat(), d.isoformat(), store, config=cfg)
        actuals = store.query_df(
            "SELECT game_pk, first_inn_runs, nrfi FROM actuals "
            "WHERE game_pk IN (" + ",".join(str(pk) for pk, _ in feats_per_game) + ")"
        )
        actual_map = {int(r.game_pk): (int(r.first_inn_runs), bool(r.nrfi))
                      for _, r in actuals.iterrows()}

        if inference is None:
            # Pure Poisson baseline replay
            for gpk, feats in feats_per_game:
                if gpk not in actual_map:
                    continue
                p = float(feats.get("poisson_p_nrfi", 0.55))
                runs, nrfi = actual_map[gpk]
                rows.append({
                    "game_pk": gpk, "game_date": d.isoformat(),
                    "p_nrfi": p, "lambda_total": float(feats.get("lambda_total", 1.0)),
                    "actual_nrfi": int(nrfi), "first_inn_runs": runs,
                    "market_p": market_provider(gpk) if market_provider else None,
                })
        else:
            game_pks = [pk for pk, _ in feats_per_game if pk in actual_map]
            f_dicts  = [feats for pk, feats in feats_per_game if pk in actual_map]
            preds = inference.predict_many(f_dicts, game_pks=game_pks)
            for pred in preds:
                runs, nrfi = actual_map[pred.game_pk]
                rows.append({
                    "game_pk": pred.game_pk, "game_date": d.isoformat(),
                    "p_nrfi": pred.nrfi_prob, "lambda_total": pred.lambda_total,
                    "actual_nrfi": int(nrfi), "first_inn_runs": runs,
                    "market_p": market_provider(pred.game_pk) if market_provider else None,
                    "color": pred.color_band, "signal": pred.signal,
                })

    df = pd.DataFrame(rows)
    if df.empty:
        log.warning("Backtest produced 0 rows — check date range / data sources")
        return BacktestReport(0, 0.0, 0.0, 0.0, 0.0, {}, None, df)

    p = df["p_nrfi"].astype(float).values
    y = df["actual_nrfi"].astype(int).values
    rb = reliability_buckets(p, y, n_bins=10)

    market_p = df["market_p"].astype(float).values if df["market_p"].notna().any() else None
    roi = simulated_roi(p, y, market_p=market_p, side="auto") if market_p is not None else None

    if save_dir is not None:
        save_dir = Path(save_dir); save_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(save_dir / f"backtest_{start_date}_{end_date}.csv", index=False)
        try:
            from .visualizations import probability_histogram, reliability_plot
            reliability_plot(rb, savepath=save_dir / "reliability.png")
            probability_histogram(p, y, savepath=save_dir / "histogram.png")
        except Exception as e:  # plotting deps optional
            log.warning("Plotting failed: %s", e)

    return BacktestReport(
        n_games=len(df),
        brier=brier_score(p, y),
        log_loss=log_loss_score(p, y),
        accuracy=float(((p >= 0.5).astype(int) == y).mean()),
        base_rate=float(y.mean()),
        reliability={
            "edges": rb.bucket_edges,
            "predicted": rb.bucket_pred_mean,
            "actual": rb.bucket_actual,
            "count": rb.bucket_count,
        },
        roi_flat=roi,
        per_game=df,
    )
