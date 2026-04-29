"""Naive baseline projection layer for MLB player props.

This is the **skeleton** projection — a league-average prior over the
prop's natural rate that the engine uses until per-player Statcast
features are wired in. It's deliberately simple:

* For batter rate markets (HR / Hits / RBI / Total Bases) we model the
  per-game count as Poisson(λ_market_implied) where λ comes from the
  market line's mid-point. Then `P(over)` = 1 − Poisson CDF at the line.
* For pitcher Strikeouts we use the same Poisson mid-point construction.

The point of this skeleton is NOT to beat the market — it's to give the
edge math a working numerator while the proper projection model
(Statcast xwOBA + park + ump factors) is built. A pure mid-point prior
will compute zero edge against the market average, so realistic edges
only appear once the projection module is replaced.

When the future model lands it should:

* Replace `project_player_market_prob` with a Statcast-driven estimate.
* Keep the same return shape (`ProjectedSide` for both Over and Under)
  so the edge layer doesn't need changes.
* Return `confidence` (∈ [0, 1]) callers can use to gate noisy
  projections.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .markets import PropMarket
from .odds_fetcher import PlayerPropLine


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectedSide:
    """One side (Over/Under or Yes/No) with a model probability."""
    market: PropMarket
    player_name: str
    line_value: float
    side: str           # 'Over' / 'Under'
    model_prob: float   # 0..1, calibrated probability the side hits
    confidence: float   # 0..1, how much weight callers should put on it


# ---------------------------------------------------------------------------
# Helpers — Poisson math (no scipy import; we keep the engine extras-free)
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
    """P(X > line) for X ~ Poisson(lam). Handles half-integer lines
    via floor() and integer lines via the strict-inequality CDF.

    The most common prop lines are 0.5, 1.5, 2.5 — non-integer cases
    where `line > line` strict-inequality reduces to `X >= ceil(line)`,
    so 1 − CDF(floor(line)).
    """
    return 1.0 - _poisson_cdf(int(math.floor(line)), lam)


# ---------------------------------------------------------------------------
# Public projection API
# ---------------------------------------------------------------------------


# Skeleton league-average rates. Each is the per-game expected count
# for an "average" MLB player making a typical-volume start (4 PA for
# a batter, ~5.7 IP for a pitcher in 2025–26 usage). These get replaced
# with per-player Statcast estimates in a follow-up PR.
_LEAGUE_RATE_PER_GAME: dict[str, float] = {
    "HR":          0.13,   # ~13% of starters HR per game (league avg)
    "Hits":        1.05,   # ~1 hit per starting batter per game
    "Total_Bases": 1.55,   # singles + doubles + triples + 4×HR
    "RBI":         0.55,
    "K":           5.20,   # average starter Ks per outing in 2025-26
}


def project_player_market_prob(
    line: PlayerPropLine, *,
    rate_override: Optional[float] = None,
) -> ProjectedSide:
    """Return a baseline projection for one prop line.

    Parameters
    ----------
    line : The PlayerPropLine to project.
    rate_override : Override the league-average λ (e.g., a real
        per-player rate from Statcast when that lands).
    """
    lam = (
        rate_override if rate_override is not None
        else _LEAGUE_RATE_PER_GAME.get(line.market.canonical, 0.0)
    )
    p_over = _prob_over_poisson(line.line_value, lam)
    side_lower = line.side.strip().lower()
    if side_lower in ("over", "yes"):
        prob = p_over
    elif side_lower in ("under", "no"):
        prob = 1.0 - p_over
    else:
        # Unknown side label — defensively project as the over side and
        # let the caller decide what to do.
        prob = p_over

    # Skeleton confidence is flat 0.30 — these projections are league-
    # priors with no per-player signal, so callers should treat the
    # output as floor evidence, not a recommendation.
    return ProjectedSide(
        market=line.market,
        player_name=line.player_name,
        line_value=line.line_value,
        side=line.side,
        model_prob=float(prob),
        confidence=0.30,
    )


def project_all(
    lines: Iterable[PlayerPropLine],
    *,
    rate_overrides: Optional[dict] = None,
) -> list[ProjectedSide]:
    """Project every line in `lines`. `rate_overrides` keys may be:
        * (player_name, canonical_market) for a per-player override
        * canonical_market for a market-wide override
    Returns the projections in input order."""
    rate_overrides = rate_overrides or {}
    out: list[ProjectedSide] = []
    for line in lines:
        rate = rate_overrides.get(
            (line.player_name, line.market.canonical),
            rate_overrides.get(line.market.canonical),
        )
        out.append(project_player_market_prob(line, rate_override=rate))
    return out
