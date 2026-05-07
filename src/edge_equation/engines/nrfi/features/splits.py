"""Helpers for computing first-inning splits and rolling-window stats.

All functions accept pandas DataFrames and return either DataFrames or
plain dicts so they're trivial to unit test offline.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


# Statcast pitch-type codes we surface as separate features. Anything
# outside this set folds into ``mix_other_pct``. The five codes here
# match the CMU NRFI capstone's top-10 features (see research notes
# in ``engines/nrfi/README.md``).
_PITCH_TYPE_CODES: tuple[str, ...] = ("FF", "SI", "FC", "CU", "CH")


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


def first_inning_pitch_mix(
    statcast_df: pd.DataFrame, pitcher_id: int,
) -> dict:
    """Per-pitch-type usage % for one pitcher's first-inning pitches.

    The CMU NRFI capstone (XGBoost, 2024) found that first-inning
    pitch-type usage --- 4-seam fastball, sinker, cutter, curveball,
    changeup --- filled 6 of the top-10 feature-importance slots
    after FIP. Anything outside the five canonical codes folds into
    ``p1_mix_other_pct``. Sums to ~1.0 (within float epsilon).

    Frame must already be filtered to inning=1. Missing pitch_type
    rows (rare on Statcast) are dropped before normalising.
    """
    if statcast_df is None or statcast_df.empty:
        return _empty_pitch_mix()
    df = statcast_df[statcast_df["pitcher"] == int(pitcher_id)]
    df = df[df["pitch_type"].notna()]
    n_total = len(df)
    if n_total == 0:
        return _empty_pitch_mix()
    counts = df["pitch_type"].astype(str).str.upper().value_counts()
    out: dict[str, float] = {}
    surfaced = 0
    for code in _PITCH_TYPE_CODES:
        c = int(counts.get(code, 0))
        out[f"p1_mix_{code.lower()}_pct"] = c / n_total
        surfaced += c
    out["p1_mix_other_pct"] = max(0.0, (n_total - surfaced) / n_total)
    out["p1_mix_pitches"] = float(n_total)
    # Arsenal depth: how many of the canonical codes a pitcher throws
    # >= 5% of the time. Trees use this as a "complexity" feature
    # without having to learn the threshold for each code separately.
    out["p1_arsenal_depth"] = float(sum(
        1 for code in _PITCH_TYPE_CODES
        if out[f"p1_mix_{code.lower()}_pct"] >= 0.05
    ))
    return out


def _empty_pitch_mix() -> dict:
    """Neutral defaults --- a slightly-fastball-heavy league prior."""
    return {
        "p1_mix_ff_pct": 0.40,
        "p1_mix_si_pct": 0.18,
        "p1_mix_fc_pct": 0.07,
        "p1_mix_cu_pct": 0.10,
        "p1_mix_ch_pct": 0.13,
        "p1_mix_other_pct": 0.12,
        "p1_mix_pitches": 0.0,
        "p1_arsenal_depth": 4.0,
    }


def first_inning_f_strike_pct(
    statcast_df: pd.DataFrame, pitcher_id: int,
) -> dict:
    """First-pitch-strike rate in inning 1 specifically.

    The strongest short-window run predictor: F-strike pitchers run
    ~3.60 expected ERA, ball-1 pitchers ~5.50. Computed as the
    fraction of plate appearances whose first pitch was a called
    strike, swinging strike, or fouled-off (anything that isn't
    ball-1).

    Returns the raw rate plus the sample (PAs) so the EB shrinker
    can regress to the league mean for low-sample pitchers.
    """
    if statcast_df is None or statcast_df.empty:
        return _empty_f_strike()
    df = statcast_df[statcast_df["pitcher"] == int(pitcher_id)]
    df = df[df["pitch_number"] == 1]
    if df.empty:
        return _empty_f_strike()
    desc = df["description"].astype(str)
    is_strike = desc.isin([
        "called_strike", "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "hit_into_play",
    ])
    n_pa = len(df)
    n_strike = int(is_strike.sum())
    return {
        "p1_f_strike_pct": n_strike / max(1, n_pa),
        "p1_f_strike_sample_pa": float(n_pa),
    }


def _empty_f_strike() -> dict:
    """League average first-pitch-strike rate is ~0.62 across MLB."""
    return {
        "p1_f_strike_pct": 0.62,
        "p1_f_strike_sample_pa": 0.0,
    }


def umpire_first_inning_stats(
    statcast_df: pd.DataFrame, ump_id: int,
) -> dict:
    """Umpire-specific first-inning called-zone behavior.

    Computes:

      * ``ump_f1_csa``       called strikes above the rulebook zone
                             expectation, per F1 called pitch. Positive
                             = wider strike zone (helps NRFI), negative
                             = tighter (drives YRFI via walks).
      * ``ump_f1_walk_rate`` walks issued / PAs in F1 specifically.
                             Distinct signal from CSA --- can be high
                             even when CSA is neutral if the umpire's
                             zone is just inconsistent.
      * ``ump_f1_pa``        sample PAs --- drives EB shrinkage.
      * ``ump_f1_called``    sample called-pitch count --- drives EB
                             shrinkage on CSA specifically.

    Frame must already be filtered to inning=1. Required columns:
    ``home_plate_umpire_id`` (or ``ump_id``), ``description``,
    ``zone``, ``events``.

    Sample-size handling: callers should pipe the raw output through
    ``f1_shrinkage.shrink_ump_f1_csa`` /
    ``f1_shrinkage.shrink_ump_f1_walk_rate`` so umpires with thin
    samples regress to the league mean.
    """
    if statcast_df is None or statcast_df.empty:
        return _empty_umpire_f1()

    ump_col = "home_plate_umpire_id" if "home_plate_umpire_id" in statcast_df.columns else "ump_id"
    df = statcast_df[statcast_df[ump_col] == int(ump_id)]
    if df.empty:
        return _empty_umpire_f1()

    # Called-pitch CSA: how often did this umpire call a strike on
    # pitches in the rulebook zone (1-9) vs out of zone (>= 10)? The
    # delta from league average is the CSA signal.
    desc = df["description"].astype(str)
    called = df[desc.isin(["called_strike", "ball"])]
    if called.empty:
        csa = 0.0
        n_called = 0
    else:
        in_zone = called["zone"].between(1, 9, inclusive="both")
        n_called = int(len(called))
        # Rulebook zone calls are strikes ~85% of the time league-wide;
        # out-of-zone calls are strikes ~9%. Blend gives the
        # umpire-specific deviation.
        in_zone_strike = (called.loc[in_zone, "description"] == "called_strike").mean() if in_zone.any() else 0.85
        oz_strike = (called.loc[~in_zone, "description"] == "called_strike").mean() if (~in_zone).any() else 0.09
        # Simple aggregate CSA: weighted average of the two deltas
        # vs league baseline.
        csa = (
            (in_zone.mean() * (in_zone_strike - 0.85))
            + ((~in_zone).mean() * (oz_strike - 0.09))
        )

    # Walk rate: walks per PA on F1 events for this umpire.
    pa_rows = df[df["events"].notna()]
    n_pa = int(len(pa_rows))
    n_bb = int(pa_rows["events"].isin(["walk", "intent_walk"]).sum())
    walk_rate = (n_bb / n_pa) if n_pa > 0 else 0.085

    return {
        "ump_f1_csa": float(csa),
        "ump_f1_walk_rate": float(walk_rate),
        "ump_f1_pa": float(n_pa),
        "ump_f1_called": float(n_called),
    }


def _empty_umpire_f1() -> dict:
    return {
        "ump_f1_csa": 0.0,
        "ump_f1_walk_rate": 0.085,
        "ump_f1_pa": 0.0,
        "ump_f1_called": 0.0,
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
