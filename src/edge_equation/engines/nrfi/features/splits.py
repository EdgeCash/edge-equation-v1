"""Helpers for computing first-inning splits and rolling-window stats.

All functions accept pandas DataFrames and return either DataFrames or
plain dicts so they're trivial to unit test offline.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def first_inning_pitcher_stats(statcast_df: pd.DataFrame, pitcher_id: int) -> dict:
    """Aggregate first-inning splits for a given pitcher from a Statcast frame.

    Expects a frame already filtered to inning=1 (cf. `fetch_statcast_first_inning`).
    Returns counting stats useful for layer-7 + layer-1 features.
    """
    if statcast_df is None or statcast_df.empty:
        return _empty_pitcher_split()

    df = statcast_df[statcast_df["pitcher"] == int(pitcher_id)].copy()
    if df.empty:
        return _empty_pitcher_split()

    pa = df["events"].notna().sum()        # one row per event = end of PA
    bf = max(int(pa), 1)
    k = (df["events"] == "strikeout").sum()
    bb = df["events"].isin(["walk", "intent_walk"]).sum()
    hbp = (df["events"] == "hit_by_pitch").sum()
    h = df["events"].isin(["single", "double", "triple", "home_run"]).sum()
    hr = (df["events"] == "home_run").sum()
    runs = df.get("post_bat_score", pd.Series([0])).diff().fillna(0).clip(lower=0).sum()

    return {
        "p1_inn_pa": int(pa),
        "p1_inn_k_pct": float(k / bf),
        "p1_inn_bb_pct": float(bb / bf),
        "p1_inn_hbp_pct": float(hbp / bf),
        "p1_inn_h_pct": float(h / bf),
        "p1_inn_hr_pct": float(hr / bf),
        "p1_inn_runs_per": float(runs / max(1, df["game_pk"].nunique())),
    }


def _empty_pitcher_split() -> dict:
    return {
        "p1_inn_pa": 0,
        "p1_inn_k_pct": 0.22,
        "p1_inn_bb_pct": 0.085,
        "p1_inn_hbp_pct": 0.01,
        "p1_inn_h_pct": 0.235,
        "p1_inn_hr_pct": 0.034,
        "p1_inn_runs_per": 0.55,  # league avg first-inning runs per side
    }


def ewma(series: Iterable[float], alpha: float = 0.4) -> float:
    """Closed-form EWMA over an iterable (newest last)."""
    s = list(series)
    if not s:
        return 0.0
    weights = np.array([(1 - alpha) ** i for i in range(len(s))][::-1])
    weights = weights / weights.sum()
    return float(np.dot(np.array(s, dtype=float), weights))


def rolling_window_stats(values: list[float], windows: tuple[int, ...] = (5, 10, 30)) -> dict:
    """Mean & std over the last N samples of a per-start log."""
    out: dict[str, float] = {}
    a = np.array(values, dtype=float) if values else np.array([np.nan])
    for w in windows:
        chunk = a[-w:]
        out[f"mean_l{w}"] = float(np.nanmean(chunk)) if chunk.size else 0.0
        out[f"std_l{w}"] = float(np.nanstd(chunk)) if chunk.size else 0.0
    return out


def blend_form(season: float, l10: float, l5: float,
               *, w_season: float = 0.30, w_l10: float = 0.40, w_l5: float = 0.30) -> float:
    """Weighted recency blend (default matches deterministic v3 engine)."""
    return w_season * season + w_l10 * l10 + w_l5 * l5


def percentile_rank(value: float, distribution: Iterable[float]) -> float:
    """Return the percentile (0-100) of `value` within `distribution`."""
    arr = np.asarray(list(distribution), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return 50.0
    return float((arr <= value).mean() * 100.0)
