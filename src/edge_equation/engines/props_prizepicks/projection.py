"""Per-player Poisson projection for MLB player props.

Replaces the Phase-4 league-average skeleton with a per-player rate
model. The pipeline is:

1. Look up the batter / pitcher's rolling rate (per-PA / per-BF) over
   the last ``lookback_days`` of Statcast events.
2. Bayesian-blend that observed rate toward the league prior with
   `prior_weight_pa` (or `prior_weight_bf`) pseudo-counts so call-ups
   don't over-fit on tiny samples.
3. Multiply by expected volume (4.1 PAs for a starting batter, 22 BFs
   for a starting pitcher — both PropsConfig-tunable) to get λ.
4. ``P(Over line) = 1 − Poisson_CDF(line, λ)``; Under = 1 − Over.
5. ``confidence`` reflects how much per-player signal we had: scales
   linearly from 0.30 (pure prior) to ~0.85 (heavy own-rate weight).

Everything routes through the same `ProjectedSide` shape the edge
module already consumes — no breaking changes to callers downstream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Union

from .config import ProjectionKnobs
from .data.statcast_loader import (
    BatterRollingRates,
    LEAGUE_BATTER_PRIOR_PER_PA,
    LEAGUE_PITCHER_PRIOR_PER_BF,
    PitcherRollingRates,
    bayesian_blend,
)
from .markets import PropMarket
from .odds_fetcher import PlayerPropLine


# ---------------------------------------------------------------------------
# Output shape — unchanged from the Phase-4 skeleton so callers don't break.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectedSide:
    """One side (Over/Under or Yes/No) with a model probability."""
    market: PropMarket
    player_name: str
    line_value: float
    side: str
    model_prob: float
    confidence: float
    # Phase-Props-1 additions for audit / dashboard:
    lam: float = 0.0           # the Poisson λ that drove model_prob
    blend_n: int = 0           # PA / BF count blended (0 → pure prior)
    blended_rate: float = 0.0  # the per-PA / per-BF rate used


# ---------------------------------------------------------------------------
# Poisson math (closed-form CDF — keeps the engine extras-free)
# ---------------------------------------------------------------------------


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X ≤ k) for X ~ Poisson(lam). Closed-form sum, fine up to k≈30."""
    if k < 0:
        return 0.0
    lam = max(0.0, float(lam))
    if lam == 0.0:
        return 1.0
    total = 0.0
    p = math.exp(-lam)
    total += p
    for i in range(1, k + 1):
        p *= lam / i
        total += p
    return min(1.0, total)


def _prob_over_poisson(line: float, lam: float) -> float:
    """P(X > line) for X ~ Poisson(lam)."""
    return 1.0 - _poisson_cdf(int(math.floor(line)), lam)


# ---------------------------------------------------------------------------
# Confidence scaling — pure prior → 0.30; full own-weight → 0.85
# ---------------------------------------------------------------------------


def _confidence_for_blend(n: int, prior_weight: float) -> float:
    """Map sample-size + prior-weight to a [0.30, 0.85] confidence.

    `n / (n + prior_weight)` is the own-rate weight; we lerp linearly
    between 0.30 (zero own weight) and 0.85 (1.0 own weight).
    """
    if n <= 0:
        return 0.30
    own_weight = n / (n + max(1.0, prior_weight))
    return 0.30 + 0.55 * float(own_weight)


# ---------------------------------------------------------------------------
# Per-player projection
# ---------------------------------------------------------------------------


PlayerRates = Union[BatterRollingRates, PitcherRollingRates, None]


# ---------------------------------------------------------------------------
# Calibration shrink — same shape as the MLB game-results temperature
# but pulled toward the vig-adjusted market price, not 0.5. Props
# markets are asymmetric (e.g. an Under at -250 isn't a coin flip), so
# pulling toward 0.5 would be wrong; pulling toward what the book
# thinks penalises Poisson's light-tail over-confidence without
# distorting picks the model isn't disagreeing with.
# ---------------------------------------------------------------------------


# Per-market temperature: 1.0 = no shrink, 0.0 = pure market.
# Tuned conservative-by-default. Operators can override at runtime.
DEFAULT_PROPS_TEMPERATURE: dict[str, float] = {
    # HR / RBI / Total_Bases are the most over-dispersed markets
    # (rare-event, fat-tailed) where Poisson under-estimates tail
    # probability the most -- aggressive shrink toward the book.
    "HR":          0.55,
    "RBI":         0.60,
    "Total_Bases": 0.65,
    # Hits / K are higher-volume per game, closer to Gaussian; less
    # shrink because the Poisson approximation is closer to truth.
    "Hits":        0.75,
    "K":           0.75,
}


def shrink_prob_toward_market(
    model_prob: float,
    market_prob_devigged: float,
    tau: float,
) -> float:
    """Bayesian-style blend: tau*model + (1-tau)*market.

    tau in [0, 1]. tau=1 keeps the raw projection, tau=0 collapses to
    market. Used as a post-hoc Platt-style shrinker on Poisson outputs
    that are too confident relative to settled outcomes.
    """
    if model_prob is None:
        return model_prob
    if market_prob_devigged is None:
        return model_prob
    tau = max(0.0, min(1.0, float(tau)))
    return tau * float(model_prob) + (1.0 - tau) * float(market_prob_devigged)


def calibrate_prob(
    model_prob: float,
    market_prob_devigged: float,
    market_canonical: str,
    temperature: Optional[dict[str, float]] = None,
) -> float:
    """Public wrapper used by both the edge builder and any backtest
    so production probs and gate-evaluation probs stay in lockstep."""
    tau = (temperature or DEFAULT_PROPS_TEMPERATURE).get(market_canonical, 1.0)
    return shrink_prob_toward_market(model_prob, market_prob_devigged, tau)


def _resolve_rate(
    line: PlayerPropLine,
    rates: PlayerRates,
    knobs: ProjectionKnobs,
) -> tuple[float, int, float]:
    """Return (per_PA_or_BF_rate, n_observed, prior_weight) for `line`.

    Falls back to the league prior when `rates` is None or the market
    has no observed value yet.
    """
    market = line.market.canonical
    if line.market.role == "batter":
        prior = LEAGUE_BATTER_PRIOR_PER_PA.get(market, 0.0)
        prior_w = knobs.prior_weight_pa
        if isinstance(rates, BatterRollingRates):
            obs = rates.get_rate(market, fallback=prior)
            n = rates.n_pa
            return bayesian_blend(obs, n, prior, prior_w), n, prior_w
        return prior, 0, prior_w
    # pitcher
    prior = LEAGUE_PITCHER_PRIOR_PER_BF.get(market, 0.0)
    prior_w = knobs.prior_weight_bf
    if isinstance(rates, PitcherRollingRates):
        obs = rates.get_rate(market, fallback=prior)
        n = rates.n_bf
        return bayesian_blend(obs, n, prior, prior_w), n, prior_w
    return prior, 0, prior_w


def project_player_market_prob(
    line: PlayerPropLine, *,
    rates: PlayerRates = None,
    knobs: Optional[ProjectionKnobs] = None,
    expected_volume: Optional[float] = None,
    rate_override: Optional[float] = None,
) -> ProjectedSide:
    """Project the side of `line` using the per-player rate (with
    league-prior blend) × expected per-game volume.

    Parameters
    ----------
    rates : Per-player rolling rates (BatterRollingRates or
        PitcherRollingRates). When None the projection uses pure
        league priors.
    knobs : ProjectionKnobs override (defaults to a fresh ProjectionKnobs()).
    expected_volume : Override PAs / BFs for the game. Defaults to
        `knobs.expected_batter_pa` for batter markets and
        `knobs.expected_pitcher_bf` for pitcher markets.
    rate_override : Per-PA / per-BF rate override. When provided, skips
        the blend layer entirely — used for backward-compat tests and
        for callers with their own projection model.
    """
    knobs = knobs or ProjectionKnobs()

    if rate_override is not None:
        # Backward-compat path: caller supplied a final rate.
        per_unit = float(rate_override)
        n_obs = 0
        # Default volume by role.
        if expected_volume is None:
            expected_volume = (
                knobs.expected_batter_pa if line.market.role == "batter"
                else knobs.expected_pitcher_bf
            )
        lam = per_unit * float(expected_volume)
        confidence = 0.30
    else:
        per_unit, n_obs, prior_w = _resolve_rate(line, rates, knobs)
        if expected_volume is None:
            expected_volume = (
                knobs.expected_batter_pa if line.market.role == "batter"
                else knobs.expected_pitcher_bf
            )
        lam = per_unit * float(expected_volume)
        confidence = _confidence_for_blend(n_obs, prior_w)

    p_over = _prob_over_poisson(line.line_value, lam)
    side_lower = (line.side or "").strip().lower()
    if side_lower in ("over", "yes"):
        prob = p_over
    elif side_lower in ("under", "no"):
        prob = 1.0 - p_over
    else:
        prob = p_over

    return ProjectedSide(
        market=line.market,
        player_name=line.player_name,
        line_value=line.line_value,
        side=line.side,
        model_prob=float(prob),
        confidence=float(confidence),
        lam=float(lam),
        blend_n=int(n_obs),
        blended_rate=float(per_unit),
    )


# ---------------------------------------------------------------------------
# Bulk projection
# ---------------------------------------------------------------------------


def project_all(
    lines: Iterable[PlayerPropLine],
    *,
    rates_by_player: Optional[dict[str, PlayerRates]] = None,
    knobs: Optional[ProjectionKnobs] = None,
    rate_overrides: Optional[dict] = None,
) -> list[ProjectedSide]:
    """Project every line. `rates_by_player` keys on `player_name` and
    yields the per-player Statcast rates; `rate_overrides` matches the
    Phase-4 API for callers passing per-player flat rates by hand.

    Returns the projections in input order so callers can `zip(lines, projections)`.
    """
    rates_by_player = rates_by_player or {}
    rate_overrides = rate_overrides or {}
    out: list[ProjectedSide] = []
    for line in lines:
        override = rate_overrides.get(
            (line.player_name, line.market.canonical),
            rate_overrides.get(line.market.canonical),
        )
        rates = rates_by_player.get(line.player_name)
        out.append(project_player_market_prob(
            line, rates=rates, knobs=knobs, rate_override=override,
        ))
    return out
