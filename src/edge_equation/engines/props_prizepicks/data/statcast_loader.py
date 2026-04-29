"""Per-batter / per-pitcher rolling-rate loader.

The props engine projects with per-player Poisson rates, not the
league-average λ that the Phase-4 skeleton shipped. This module is
the data side of that upgrade:

* `load_batter_rates(player_id, end_date, days)` — pulls the player's
  last `days` of Statcast events, computes per-PA rates for HR, Hits,
  Total Bases, and RBI.
* `load_pitcher_rates(player_id, end_date, days)` — pulls the
  pitcher's last `days` of Statcast events, computes per-BF rates for
  Strikeouts.
* `bayesian_blend(observed, n_observed, prior, prior_weight_pa)` —
  shrinks small-sample observed rates toward the league prior so
  call-up batters with 12 PAs don't get projected as Babe Ruth.

Cache layer
-----------

Hits the shared parquet cache (``edge_equation.utils.caching``) under
namespace ``statcast_player`` keyed on ``{player_id}_{end_date}_{days}``.
A walk-forward backtest replays cleanly because re-fetching the same
window returns the same parquet.

Network resilience
------------------

* `_import_pybaseball()` lazy-imports so unit tests can mock the
  loader without pybaseball installed.
* All errors fall through to the league-average prior — projection
  callers always get a number, never an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from edge_equation.utils.caching import read_parquet, write_parquet
from edge_equation.utils.logging import get_logger

from ..config import PropsConfig, get_default_config

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# League-average priors (per-PA / per-BF). Sourced from 2025-26 MLB
# season aggregates; updated annually as part of the offseason refresh.
# These are the fall-back rates when a player has no Statcast history.
# ---------------------------------------------------------------------------


LEAGUE_BATTER_PRIOR_PER_PA: dict[str, float] = {
    "HR":          0.030,   # ~3% of PAs end in a homer (league avg)
    "Hits":        0.245,   # league BA across PAs
    "Total_Bases": 0.395,   # league SLG-equivalent per PA
    "RBI":         0.115,
}

LEAGUE_PITCHER_PRIOR_PER_BF: dict[str, float] = {
    "K": 0.230,             # ~23% K rate league-wide
}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatterRollingRates:
    """Per-batter rolling rates over a `lookback_days` window."""
    player_id: int
    player_name: str
    n_pa: int
    end_date: str
    lookback_days: int
    rate_per_pa: dict[str, float] = field(default_factory=dict)

    def get_rate(self, market: str, *, fallback: Optional[float] = None) -> float:
        """Return the per-PA rate for `market`, or fallback / 0.0."""
        if market in self.rate_per_pa:
            return self.rate_per_pa[market]
        if fallback is not None:
            return fallback
        return LEAGUE_BATTER_PRIOR_PER_PA.get(market, 0.0)


@dataclass(frozen=True)
class PitcherRollingRates:
    """Per-pitcher rolling rates over a `lookback_days` window."""
    player_id: int
    player_name: str
    n_bf: int
    end_date: str
    lookback_days: int
    rate_per_bf: dict[str, float] = field(default_factory=dict)

    def get_rate(self, market: str, *, fallback: Optional[float] = None) -> float:
        if market in self.rate_per_bf:
            return self.rate_per_bf[market]
        if fallback is not None:
            return fallback
        return LEAGUE_PITCHER_PRIOR_PER_BF.get(market, 0.0)


# ---------------------------------------------------------------------------
# Bayesian shrinkage helper
# ---------------------------------------------------------------------------


def bayesian_blend(
    observed_rate: float, n_observed: int,
    prior_rate: float, prior_weight: float,
) -> float:
    """Shrink `observed_rate` toward `prior_rate` using `prior_weight`
    pseudo-counts.

    Standard textbook Bayesian estimate when both rates are Bernoulli /
    Poisson per-PA (same form):

        blended = (n_obs * obs + w * prior) / (n_obs + w)

    With `prior_weight = 80` for batter rate stats, an everyday
    starter (~200 PA in 60 days) gets ~71% own weight; a call-up with
    20 PAs gets ~20% own weight. Matches Tango's "200 PA stabilizes"
    rule of thumb after one full month.
    """
    n_obs = max(0, int(n_observed))
    if n_obs == 0:
        return float(prior_rate)
    return (n_obs * observed_rate + prior_weight * prior_rate) / (
        n_obs + prior_weight
    )


# ---------------------------------------------------------------------------
# pybaseball lazy import
# ---------------------------------------------------------------------------


def _import_pybaseball():
    try:
        import pybaseball  # type: ignore
        return pybaseball
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "pybaseball is required for Statcast pulls. "
            "Install via `pip install -e .[nrfi]`."
        ) from e


# ---------------------------------------------------------------------------
# Statcast pull (cached)
# ---------------------------------------------------------------------------


def _date_window(end_date: str, days: int) -> tuple[str, str]:
    from datetime import date, timedelta
    end = date.fromisoformat(end_date)
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def fetch_player_statcast_window(
    player_id: int, *,
    end_date: str,
    days: int = 60,
    role: str,                  # 'batter' or 'pitcher'
    config: Optional[PropsConfig] = None,
):
    """Return a Statcast DataFrame for `player_id` over the rolling window.

    Cached as parquet under ``<cache_dir>/parquet/statcast_player/`` so a
    repeated call (or a walk-forward backtest) is a no-op. Returns None
    when the cache miss + live fetch both fail — caller must handle
    None as "fall back to league prior".
    """
    cfg = (config or get_default_config()).resolve_paths()
    start, end = _date_window(end_date, days)
    key = f"{role}_{player_id}_{start}_{end}".replace("-", "")
    cached = read_parquet(cfg.cache_dir, "statcast_player", key)
    if cached is not None:
        return cached

    try:
        pyb = _import_pybaseball()
    except ImportError as e:
        log.warning("pybaseball unavailable: %s", e)
        return None

    fetcher = (
        pyb.statcast_batter if role == "batter"
        else pyb.statcast_pitcher
    )
    try:
        df = fetcher(start, end, player_id)
    except Exception as e:
        log.warning(
            "Statcast %s fetch failed for player_id=%s window=%s..%s: %s",
            role, player_id, start, end, e,
        )
        return None
    if df is None or df.empty:
        # Cache the empty frame so we don't keep re-pulling for low-PA
        # players or DL'd starters.
        try:
            write_parquet(df, cfg.cache_dir, "statcast_player", key)
        except Exception:
            pass
        return df
    write_parquet(df, cfg.cache_dir, "statcast_player", key)
    return df


# ---------------------------------------------------------------------------
# Rate computation
# ---------------------------------------------------------------------------


# Statcast `events` column values that count for each prop market. Only
# end-of-PA outcomes appear here — pitches without a terminal event
# carry an empty / NaN events value and are filtered out below.
_BATTER_EVENT_TO_MARKETS: dict[str, dict[str, float]] = {
    # event_string → {market: weighted_count}
    "single":            {"Hits": 1.0, "Total_Bases": 1.0},
    "double":            {"Hits": 1.0, "Total_Bases": 2.0},
    "triple":            {"Hits": 1.0, "Total_Bases": 3.0},
    "home_run":          {"Hits": 1.0, "Total_Bases": 4.0, "HR": 1.0},
}


def compute_batter_rates_from_statcast(
    df, *, player_id: int, player_name: str,
    end_date: str, lookback_days: int,
) -> BatterRollingRates:
    """Aggregate per-PA rates from a Statcast events frame.

    PAs == rows with a non-null `events` column (Statcast convention:
    every plate-appearance terminus emits one row with `events` set).
    RBI is read directly from the `rbi` column when present, summed
    across PAs, divided by PA count.
    """
    rates: dict[str, float] = {}
    n_pa = 0
    if df is not None and not df.empty and "events" in df.columns:
        events = df.dropna(subset=["events"])
        n_pa = int(len(events))
        if n_pa > 0:
            counts = {"Hits": 0.0, "Total_Bases": 0.0, "HR": 0.0}
            for ev in events["events"].astype(str).tolist():
                weights = _BATTER_EVENT_TO_MARKETS.get(ev, {})
                for market, w in weights.items():
                    counts[market] = counts.get(market, 0.0) + w
            rates = {m: counts[m] / n_pa for m in counts}
            if "rbi" in df.columns:
                rbi_sum = float(events["rbi"].fillna(0).sum())
                rates["RBI"] = rbi_sum / n_pa
    return BatterRollingRates(
        player_id=int(player_id), player_name=str(player_name),
        n_pa=n_pa, end_date=end_date,
        lookback_days=int(lookback_days),
        rate_per_pa=rates,
    )


def compute_pitcher_rates_from_statcast(
    df, *, player_id: int, player_name: str,
    end_date: str, lookback_days: int,
) -> PitcherRollingRates:
    """Aggregate per-BF Ks rate from a pitcher's Statcast frame.

    BF == rows with a non-null `events` column (each PA-terminus row).
    K count = events == 'strikeout'.
    """
    rates: dict[str, float] = {}
    n_bf = 0
    if df is not None and not df.empty and "events" in df.columns:
        events = df.dropna(subset=["events"])
        n_bf = int(len(events))
        if n_bf > 0:
            ks = int((events["events"].astype(str) == "strikeout").sum())
            rates["K"] = ks / n_bf
    return PitcherRollingRates(
        player_id=int(player_id), player_name=str(player_name),
        n_bf=n_bf, end_date=end_date,
        lookback_days=int(lookback_days),
        rate_per_bf=rates,
    )


# ---------------------------------------------------------------------------
# Public load API
# ---------------------------------------------------------------------------


def load_batter_rates(
    player_id: int, *,
    player_name: str = "",
    end_date: str,
    days: int = 60,
    config: Optional[PropsConfig] = None,
) -> BatterRollingRates:
    """One-shot: fetch Statcast → compute per-PA rates. Empty rates
    when the player has no recent data — callers blend with the
    league prior."""
    df = fetch_player_statcast_window(
        player_id, end_date=end_date, days=days, role="batter",
        config=config,
    )
    return compute_batter_rates_from_statcast(
        df, player_id=player_id, player_name=player_name,
        end_date=end_date, lookback_days=days,
    )


def load_pitcher_rates(
    player_id: int, *,
    player_name: str = "",
    end_date: str,
    days: int = 60,
    config: Optional[PropsConfig] = None,
) -> PitcherRollingRates:
    df = fetch_player_statcast_window(
        player_id, end_date=end_date, days=days, role="pitcher",
        config=config,
    )
    return compute_pitcher_rates_from_statcast(
        df, player_id=player_id, player_name=player_name,
        end_date=end_date, lookback_days=days,
    )
