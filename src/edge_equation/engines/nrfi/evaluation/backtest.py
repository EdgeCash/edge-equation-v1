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

    from edge_equation.engines.nrfi.evaluation.backtest import backtest_range
    report = backtest_range("2024-04-01", "2024-04-30")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
    FeatureBuilder, GameContext, LineupInputs, PitchArsenal, PitcherInputs,
    UmpireInputs, features_to_blob,
)
from ..features.splits import (
    first_inning_f_strike_pct,
    first_inning_pitch_mix,
    first_inning_pitcher_stats,
    umpire_first_inning_stats,
)
from ..models.inference import NRFIInferenceEngine
from ..models.model_training import TrainedBundle
from edge_equation.utils.logging import get_logger
from .metrics import RoiReport, brier_score, log_loss_score, reliability_buckets, simulated_roi

log = get_logger(__name__)


@dataclass
class RegimeMetrics:
    """Headline metrics for a single ABS regime slice."""
    label: str           # "pre_abs_2024_2025" or "abs_2026_plus"
    n_games: int
    brier: float
    log_loss: float
    accuracy: float
    base_rate: float


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
    regimes: list[RegimeMetrics] = field(default_factory=list)


def _date_iter(start: str, end: str) -> Iterable[date]:
    cur = date.fromisoformat(start); end_d = date.fromisoformat(end)
    while cur <= end_d:
        yield cur
        cur += timedelta(days=1)


def _season_from_date(target_date: str) -> int:
    return int(target_date[:4])


def _abs_active_for_season(season: int) -> bool:
    """ABS Challenge System became league-wide in 2026."""
    return season >= 2026


def _first_inn_or_empty(statcast_df, pitcher_id: int) -> dict:
    """Wrap the F1 pitcher-stats aggregator with the empty-frame guard
    so the call site is one line."""
    if statcast_df is None or statcast_df.empty or not pitcher_id:
        return {}
    return first_inning_pitcher_stats(statcast_df, pitcher_id)


def _arsenal_for_pitcher(statcast_df, pitcher_id: int) -> PitchArsenal:
    """Build a PitchArsenal populated with the pitcher's F1-specific
    pitch-mix usage + first-pitch-strike rate, on top of the existing
    league-prior defaults for the season-wide arsenal fields.

    Empty Statcast frames return the default neutral PitchArsenal --
    the model still gets a valid feature row, just with sample-size
    columns at 0 so the EB shrinkage in ``_pitcher_layer`` regresses
    everything to the league prior.

    Honors ``NRFI_DISABLE_F1_V2`` for A/B testing --- when set, returns
    the default PitchArsenal so the operator can compare retrains with
    and without the v2 features without changing code.
    """
    from .feature_diff import v2_features_disabled
    arsenal = PitchArsenal()
    if v2_features_disabled():
        return arsenal
    if statcast_df is None or statcast_df.empty or not pitcher_id:
        return arsenal
    mix = first_inning_pitch_mix(statcast_df, pitcher_id)
    fstrike = first_inning_f_strike_pct(statcast_df, pitcher_id)
    return PitchArsenal(
        # Carry the season-wide defaults (velo, spin, csw, etc.) the
        # caller hasn't overridden. Future PR can wire a 30-day
        # rolling pull for those too.
        fb_velo_mph=arsenal.fb_velo_mph,
        fb_spin_rpm=arsenal.fb_spin_rpm,
        fb_iv_movement_in=arsenal.fb_iv_movement_in,
        secondary_whiff_pct=arsenal.secondary_whiff_pct,
        arsenal_count=arsenal.arsenal_count,
        csw_pct=arsenal.csw_pct,
        zone_pct=arsenal.zone_pct,
        chase_pct=arsenal.chase_pct,
        # F1-specific fields populated from the splits aggregators.
        f1_mix_ff_pct=float(mix.get("p1_mix_ff_pct", arsenal.f1_mix_ff_pct)),
        f1_mix_si_pct=float(mix.get("p1_mix_si_pct", arsenal.f1_mix_si_pct)),
        f1_mix_fc_pct=float(mix.get("p1_mix_fc_pct", arsenal.f1_mix_fc_pct)),
        f1_mix_cu_pct=float(mix.get("p1_mix_cu_pct", arsenal.f1_mix_cu_pct)),
        f1_mix_ch_pct=float(mix.get("p1_mix_ch_pct", arsenal.f1_mix_ch_pct)),
        f1_arsenal_depth=float(
            mix.get("p1_arsenal_depth", arsenal.f1_arsenal_depth),
        ),
        f_strike_pct=float(
            fstrike.get("p1_f_strike_pct", arsenal.f_strike_pct),
        ),
        f_strike_sample_pa=float(
            fstrike.get("p1_f_strike_sample_pa", arsenal.f_strike_sample_pa),
        ),
    )


def _is_opener(g, side: str) -> bool:
    """Best-effort opener / bullpen-day flag for the day's probable.

    Looks for an explicit ``home_pitcher_role`` / ``away_pitcher_role``
    column on the schedule row when the upstream ETL has flagged it.
    Falls back to ``False`` when the row doesn't carry the field, so
    older backfills (no role column) silently keep their starter-based
    feature shape. The opener interaction in
    ``_interactions._pitcher_opener_x_kpct`` then no-ops cleanly.

    Honors ``NRFI_DISABLE_F1_V2`` for A/B testing.

    A future PR can sharpen this by parsing MLB Stats API's
    ``probable_pitcher`` block, which sometimes labels openers
    explicitly, or by checking the pitcher's average IP / start over
    the last 5 outings (< 3.0 IP avg = strong opener signal).
    """
    from .feature_diff import v2_features_disabled
    if v2_features_disabled():
        return False
    role_attr = f"{side}_pitcher_role"
    role = getattr(g, role_attr, None)
    if not role:
        return False
    return str(role).strip().lower() in {"opener", "bullpen", "reliever"}


def reconstruct_features_for_date(
    target_date: str,
    *,
    store: NRFIStore,
    statcast_window_days: int = 30,
    config: NRFIConfig | None = None,
    forecast_weather_only: bool = False,
) -> list[tuple[int, dict]]:
    """Build features for every game on `target_date` using only
    information available before first pitch.

    Parameters
    ----------
    forecast_weather_only : When True, weather is pulled from the
        Open-Meteo *forecast* endpoint snapped to the hour 3 hours
        before first pitch — i.e., what the daily run would have
        actually seen at slate-build time. Default False uses the
        archive endpoint (real observations) which is the right
        choice for measuring calibration but slightly leaks vs. the
        live forecast.

    Returns a list of (game_pk, feature_dict) tuples.
    """
    cfg = config or get_default_config()
    # Regime-aware ABS toggle: pre-2026 seasons get ABS off automatically
    # so backtest replay applies the umpire-attenuation rules to the
    # right historical data. Override by passing a custom config.
    season = _season_from_date(target_date)
    if cfg.enable_abs_2026 != _abs_active_for_season(season):
        from dataclasses import replace
        cfg = replace(cfg, enable_abs_2026=_abs_active_for_season(season))
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

    # Helpers — DuckDB rows arrive via pandas, so any nullable column
    # may be `pd.NA`. Truthy checks (`x or 0`, `if x:`) raise on NA, so
    # we route every nullable through these guards.
    def _safe_int(v, default=None):
        if v is None or pd.isna(v):
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _safe_str(v, default=""):
        if v is None or pd.isna(v):
            return default
        return str(v)

    out: list[tuple[int, dict]] = []
    try:
        for _, g in games.iterrows():
            try:
                # Resolve park: try the stored venue code first, then fall
                # back to the home-team tricode (always reliable). This keeps
                # us robust to mid-season venue renames (e.g. 2026: Guaranteed
                # Rate Field → Rate Field).
                park = None
                for candidate in (_safe_str(g.venue_code), _safe_str(g.home_team)):
                    if not candidate:
                        continue
                    try:
                        park = park_for(candidate)
                        break
                    except KeyError:
                        continue
                if park is None:
                    log.warning("Unknown park %s (home=%s) — skipping game %s",
                                 g.venue_code, g.home_team, g.game_pk)
                    continue

                # Weather — archive (real observations) by default, forecast
                # snapped to T-3hr when forecast_weather_only=True or when the
                # requested date is in the future relative to the archive's
                # coverage (live daily runs hit this for late West Coast games
                # whose UTC first_pitch_ts rolls over to the next calendar day).
                wx = None
                fp_ts = _safe_str(g.first_pitch_ts)
                if fp_ts:
                    fp = datetime.fromisoformat(fp_ts.replace("Z", "+00:00"))
                    today_utc = datetime.now(fp.tzinfo).date()
                    pitch_date = fp.date()
                    use_forecast = forecast_weather_only or pitch_date >= today_utc
                    if use_forecast:
                        target_iso = (fp - timedelta(hours=3)).isoformat()
                        wx = weather.forecast(park.lat, park.lon, target_iso, park.altitude_ft)
                    else:
                        wx = weather.archive(park.lat, park.lon, fp_ts, park.altitude_ft)
                    if wx is not None:
                        store.upsert("weather", [{
                            "game_pk": _safe_int(g.game_pk, default=0),
                            "source": wx.source,
                            "as_of_ts": fp_ts,
                            "temperature_f": wx.temperature_f,
                            "wind_speed_mph": wx.wind_speed_mph,
                            "wind_dir_deg": wx.wind_dir_deg,
                            "humidity_pct": wx.humidity_pct,
                            "dew_point_f": wx.dew_point_f,
                            "air_density": wx.air_density_kg_m3,
                            "precip_prob": wx.precip_prob,
                            "roof_open": None,
                        }])

                # Pitcher inputs (first-inning splits + pitch-mix +
                # F-strike from Statcast). The ``_arsenal_for_pitcher``
                # helper builds the F1-specific PitchArsenal block; an
                # empty Statcast frame falls back to neutral league
                # priors (still valid feature row, just zeroed sample).
                home_pid = _safe_int(g.home_pitcher_id, default=0)
                away_pid = _safe_int(g.away_pitcher_id, default=0)
                home_p = PitcherInputs(
                    pitcher_id=home_pid,
                    hand=_safe_str(g.home_pitcher_hand) or "R",
                    first_inn_stats=_first_inn_or_empty(statcast_df, home_pid),
                    arsenal=_arsenal_for_pitcher(statcast_df, home_pid),
                    is_opener=_is_opener(g, "home"),
                )
                away_p = PitcherInputs(
                    pitcher_id=away_pid,
                    hand=_safe_str(g.away_pitcher_hand) or "R",
                    first_inn_stats=_first_inn_or_empty(statcast_df, away_pid),
                    arsenal=_arsenal_for_pitcher(statcast_df, away_pid),
                    is_opener=_is_opener(g, "away"),
                )

                # Lineup inputs — defaults are league averages; richer lookups
                # would join with batter season stats from pybaseball.
                home_lu = LineupInputs(confirmed=bool(_safe_str(g.home_lineup)))
                away_lu = LineupInputs(confirmed=bool(_safe_str(g.away_lineup)))

                # Umpire F1-specific splits feed the chain we believe in:
                # tight zone -> walks to top-of-order -> runs. EB-shrunk
                # to league mean inside the umpire feature layer when
                # sample is thin. Honors NRFI_DISABLE_F1_V2 for A/B.
                from .feature_diff import v2_features_disabled
                ump_id = _safe_int(g.ump_id)
                ump_f1 = (
                    umpire_first_inning_stats(statcast_df, ump_id)
                    if (not v2_features_disabled()
                         and statcast_df is not None
                         and not statcast_df.empty
                         and ump_id)
                    else {}
                )
                ump = UmpireInputs(
                    ump_id=ump_id,
                    full_name="",  # pulled from `umpires` table when present
                    f1_csa=float(ump_f1.get("ump_f1_csa", 0.0)),
                    f1_walk_rate=float(ump_f1.get("ump_f1_walk_rate", 0.085)),
                    f1_called_sample=float(ump_f1.get("ump_f1_called", 0.0)),
                    f1_pa_sample=float(ump_f1.get("ump_f1_pa", 0.0)),
                )

                gpk = _safe_int(g.game_pk, default=0)
                ctx = GameContext(
                    game_pk=gpk,
                    game_date=_safe_str(g.game_date),
                    season=_safe_int(g.season, default=int(target_date[:4])),
                    home_team=_safe_str(g.home_team),
                    away_team=_safe_str(g.away_team),
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
                out.append((gpk, feats))
                store.upsert("features", [{
                    "game_pk": gpk,
                    "model_version": "elite_nrfi_v1",
                    "feature_blob": features_to_blob(feats),
                }])
            except Exception as game_err:
                # One bad game shouldn't tank the whole slate — log and move on.
                log.warning("Feature build failed for game %s: %s",
                             getattr(g, "game_pk", "?"), game_err)
                continue
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
    forecast_weather_only: bool = False,
    roi_green_only: bool = False,
    green_threshold: float = 0.70,
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
            feats_per_game = reconstruct_features_for_date(
                d.isoformat(),
                store=store, config=cfg,
                forecast_weather_only=forecast_weather_only,
            )
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
    if market_p is not None:
        if roi_green_only:
            # Restrict ROI sim to "green" picks only — the high-confidence
            # spots the user actually bets. Mask both probs and outcomes
            # so the call is point-in-time consistent.
            mask = (p >= green_threshold) | (p <= (1.0 - green_threshold))
            if mask.any():
                roi = simulated_roi(p[mask], y[mask],
                                     market_p=market_p[mask], side="auto")
            else:
                roi = None
        else:
            roi = simulated_roi(p, y, market_p=market_p, side="auto")
    else:
        roi = None

    # Per-regime slices (pre-ABS 2024-2025 vs post-ABS 2026+).
    df["_season"] = df["game_date"].str[:4].astype(int)
    regime_metrics: list[RegimeMetrics] = []
    for label, mask in (
        ("pre_abs_2024_2025", df["_season"] < 2026),
        ("abs_2026_plus",      df["_season"] >= 2026),
    ):
        sub = df[mask]
        if sub.empty:
            continue
        sp = sub["p_nrfi"].astype(float).values
        sy = sub["actual_nrfi"].astype(int).values
        regime_metrics.append(RegimeMetrics(
            label=label,
            n_games=len(sub),
            brier=brier_score(sp, sy),
            log_loss=log_loss_score(sp, sy),
            accuracy=float(((sp >= 0.5).astype(int) == sy).mean()),
            base_rate=float(sy.mean()),
        ))

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
        regimes=regime_metrics,
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


# ---------------------------------------------------------------------------
# Summary table — concise human-readable summary for the CLI
# ---------------------------------------------------------------------------

def summary_table_str(report: BacktestReport, *,
                       green_threshold: float = 0.70) -> str:
    """Return the audit-style summary table for a backtest report.

    Layout::

        ┌────────────────────────────────────────────────┐
        │  N games          812                          │
        │  Base NRFI rate   53.6%                        │
        │  Accuracy@.5      62.4%                        │
        │  Brier            0.2168                       │
        │  Log loss         0.6342                       │
        │  Pre-ABS  n=523   acc 61.8%   brier 0.2185    │
        │  ABS-era  n=289   acc 63.7%   brier 0.2138    │
        │  Green-only ROI   +18.4u (n=47, edge 6.1pp)   │
        │  Top insights:                                  │
        │    * Engine outperformed pre-ABS by 1.9pp acc   │
        │    * High-confidence greens hit 74% (target 70+)│
        └────────────────────────────────────────────────┘
    """
    lines: list[str] = []
    lines.append("Backtest summary")
    lines.append("─" * 50)
    lines.append(f"  N games           {report.n_games}")
    lines.append(f"  Base NRFI rate    {report.base_rate * 100:.1f}%")
    lines.append(f"  Accuracy@.5       {report.accuracy * 100:.1f}%")
    lines.append(f"  Brier             {report.brier:.4f}")
    lines.append(f"  Log loss          {report.log_loss:.4f}")

    for r in report.regimes:
        tag = "Pre-ABS" if r.label.startswith("pre_abs") else "ABS-era"
        lines.append(
            f"  {tag:<10} n={r.n_games:>4}   acc {r.accuracy * 100:.1f}%"
            f"   brier {r.brier:.4f}"
        )

    if report.roi_flat:
        rf = report.roi_flat
        lines.append(
            f"  ROI               {rf.units_won:+.2f}u  "
            f"(n={rf.bets}, edge {rf.avg_edge_pct:.2f}pp, "
            f"ROI {rf.roi_pct:+.2f}%)"
        )

    # Insights — derived from the per-game frame
    insights = _derive_insights(report, green_threshold=green_threshold)
    if insights:
        lines.append("  Insights:")
        for ins in insights:
            lines.append(f"    * {ins}")

    return "\n".join(lines)


def _derive_insights(report: BacktestReport, *,
                      green_threshold: float = 0.70) -> list[str]:
    df = report.per_game
    if df is None or df.empty:
        return []
    out: list[str] = []
    p = df["p_nrfi"].astype(float).values
    y = df["actual_nrfi"].astype(int).values

    # Hit rate among green spots.
    green = p >= green_threshold
    if green.sum() >= 5:
        hr = float(y[green].mean())
        out.append(f"Green-confidence ({int(green_threshold*100)}+) "
                    f"NRFI hit rate: {hr * 100:.1f}% (n={int(green.sum())})")

    # Hit rate among red spots (low NRFI prob → expect YRFI hits).
    red = p <= (1.0 - green_threshold)
    if red.sum() >= 5:
        hr = float((1 - y[red]).mean())
        out.append(f"Red-confidence ({int((1 - green_threshold)*100)}-) "
                    f"YRFI hit rate: {hr * 100:.1f}% (n={int(red.sum())})")

    # Regime delta
    if len(report.regimes) >= 2:
        a, b = report.regimes[0], report.regimes[1]
        delta_acc = (b.accuracy - a.accuracy) * 100
        sign = "+" if delta_acc >= 0 else ""
        out.append(
            f"Engine accuracy ABS-era − pre-ABS: {sign}{delta_acc:.1f}pp"
        )
    return out

