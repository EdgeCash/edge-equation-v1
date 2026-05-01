"""Full-Game explanations — MC bands + decomposition Why notes.

Two helpers consumed by the daily orchestrator to populate the
``FullGameOutput`` payload's audit fields. Pure-Python, no extras
required (the projection layer already imports nothing beyond stdlib
+ the engine config).

* ``mc_band`` — bootstrap a 5/95 confidence band on the side's
  probability by jittering the Poisson rates (multiplicative
  log-normal) and recomputing the closed-form Poisson / Skellam
  probability. Mirrors NRFI's `mc_band_pp` UX: the operator sees
  how wide the model's posterior is, not just the point estimate.

* ``decomposition_drivers`` — produces a short list of human-readable
  "Why" bullets explaining what drove the projection. The Poisson
  model is deterministic so we can decompose exactly: how much of
  the projected λ came from the league prior vs the team's own
  rolling rate, the home / away contributions, and the edge framing.
  No SHAP library needed.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional

from .projection import (
    ProjectedFullGameSide,
    _prob_over_poisson,
    _skellam_p_diff_gt,
)


# ---------------------------------------------------------------------------
# MC bands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCBand:
    """Monte-Carlo confidence band for a projected probability.

    Both endpoints are in [0, 1]. ``low``/``high`` are the 5th/95th
    percentiles of the bootstrap distribution of the side's probability
    under multiplicative log-normal jitter on (λ_home, λ_away).
    """
    low: float
    high: float
    n_samples: int

    @property
    def band_pp(self) -> float:
        """Width of the band in percentage points (0..100)."""
        return round(max(0.0, self.high - self.low) * 100.0, 1)


def mc_band(
    proj: ProjectedFullGameSide, *,
    line_value: Optional[float] = None,
    is_home_side: Optional[bool] = None,
    f5_share: float = 0.55,
    n_samples: int = 2000,
    rate_jitter: float = 0.10,
    seed: int = 42,
) -> MCBand:
    """Bootstrap a 5/95 confidence band around the projection's probability.

    Two sources of uncertainty get sampled:

    1. Per-team rate noise — perturb λ_home and λ_away by independent
       multiplicative log-normal jitter (default σ = 10%) to model
       "we got the team-strength estimates slightly wrong."
    2. Closed-form market math on each jittered (λ_h, λ_j) pair —
       Poisson CDF for over/under markets, Skellam tail for ML /
       Run_Line. No need to sample integer outcomes.

    Parameters
    ----------
    line_value : Override the projection's persisted line. Only the
        Run_Line / Total / Team_Total markets care; ML / F5_ML ignore it.
    is_home_side : True when the staked side is the home tricode.
        Defaults to True for over_under markets and False otherwise —
        callers building the band from a populated `FullGameOutput`
        should pass the explicit flag.
    """
    rng = random.Random(seed)
    market = proj.market.canonical
    side_lower = (proj.side or "").strip().lower()
    if line_value is None:
        line_value = proj.line_value
    if is_home_side is None:
        is_home_side = side_lower in ("over",)

    lam_h = max(0.0, float(proj.lam_home))
    lam_a = max(0.0, float(proj.lam_away))
    if (lam_h + lam_a) <= 0.0 or n_samples <= 0:
        return MCBand(low=proj.model_prob, high=proj.model_prob, n_samples=0)

    samples: list[float] = []
    for _ in range(n_samples):
        jh = math.exp(rng.gauss(0.0, rate_jitter))
        ja = math.exp(rng.gauss(0.0, rate_jitter))
        lh = lam_h * jh
        la = lam_a * ja
        p = _prob_for_market(
            market=market, side_lower=side_lower, line_value=line_value,
            is_home_side=is_home_side, lam_home=lh, lam_away=la,
            f5_share=f5_share,
        )
        if p is None:
            continue
        samples.append(max(0.0, min(1.0, p)))

    if not samples:
        return MCBand(low=proj.model_prob, high=proj.model_prob, n_samples=0)

    samples.sort()
    lo_idx = max(0, int(0.05 * len(samples)) - 1)
    hi_idx = min(len(samples) - 1, int(0.95 * len(samples)))
    return MCBand(
        low=round(samples[lo_idx], 4),
        high=round(samples[hi_idx], 4),
        n_samples=len(samples),
    )


def _prob_for_market(
    *, market: str, side_lower: str, line_value: Optional[float],
    is_home_side: bool, lam_home: float, lam_away: float, f5_share: float,
) -> Optional[float]:
    """Closed-form probability for one (market, side) under given λs.

    Returns None when the market/line combination doesn't yield a
    well-defined probability (e.g. Total without a line value).
    """
    lam_total = lam_home + lam_away
    if market == "Total":
        if line_value is None:
            return None
        p_over = _prob_over_poisson(float(line_value), lam_total)
        return p_over if side_lower == "over" else (1.0 - p_over)
    if market == "F5_Total":
        if line_value is None:
            return None
        p_over = _prob_over_poisson(float(line_value), lam_total * f5_share)
        return p_over if side_lower == "over" else (1.0 - p_over)
    if market == "Team_Total":
        if line_value is None:
            return None
        team_lam = lam_home if is_home_side else lam_away
        p_over = _prob_over_poisson(float(line_value), team_lam)
        return p_over if side_lower == "over" else (1.0 - p_over)
    if market == "ML":
        return _skellam_p_diff_gt(0.0, lam_home, lam_away) if is_home_side \
            else _skellam_p_diff_gt(0.0, lam_away, lam_home)
    if market == "F5_ML":
        f5_h = lam_home * f5_share
        f5_a = lam_away * f5_share
        return _skellam_p_diff_gt(0.0, f5_h, f5_a) if is_home_side \
            else _skellam_p_diff_gt(0.0, f5_a, f5_h)
    if market == "Run_Line":
        if line_value is None:
            return None
        threshold = -float(line_value)
        return _skellam_p_diff_gt(threshold, lam_home, lam_away) if is_home_side \
            else _skellam_p_diff_gt(threshold, lam_away, lam_home)
    return None


# ---------------------------------------------------------------------------
# Decomposition — "Why" bullets
# ---------------------------------------------------------------------------


def decomposition_drivers(
    proj: ProjectedFullGameSide, *,
    home_tricode: str = "",
    away_tricode: str = "",
    prior_weight: float = 12.0,
    market_prob: Optional[float] = None,
    edge_pp: Optional[float] = None,
) -> list[str]:
    """Build short "Why" bullets explaining the projection.

    The full-game projection is exact: λ = blended_team_strength ×
    league_rpg × matchup_pitching_strength. We expose the team
    contributions in plain language so the operator (and the public
    reader) can see whether the call rests on real per-team signal or
    on the league prior.

    Bullets returned (most to least significant):

    1. **Sample-size split** — "78% own form / 22% league prior" so
       the reader knows whether the call leans on signal or the
       fallback. Uses the smaller of (home_n, away_n) since one team
       on a thin sample limits the projection's confidence.
    2. **λ build-up** — "λ_home 4.85 + λ_away 4.20 → 9.05" so a curious
       reader can run the math themselves.
    3. **Edge framing** (only when market_prob is provided) — keeps
       the reader oriented to *why this is a bet*, not just a forecast.

    Bullets are capped at 3-4 — the driver list ends up in the email
    card and the API payload; verbosity hurts both.
    """
    bullets: list[str] = []

    # 1. Sample-size split
    n_h = max(0, int(proj.blend_n_home))
    n_a = max(0, int(proj.blend_n_away))
    n = min(n_h, n_a)
    pw = max(1e-6, float(prior_weight))
    own_weight = n / (n + pw) if n > 0 else 0.0
    prior_weight_share = 1.0 - own_weight
    own_pct = round(own_weight * 100.0)
    prior_pct = round(prior_weight_share * 100.0)
    if n == 0:
        bullets.append(
            "No team-rate data yet — projection rests entirely on the "
            "league prior."
        )
    elif own_pct >= 70:
        bullets.append(
            f"{own_pct}% weight on team form ({n} games min) vs "
            f"{prior_pct}% league prior."
        )
    elif own_pct >= 35:
        bullets.append(
            f"Balanced read: {own_pct}% team form ({n} games min), "
            f"{prior_pct}% league prior."
        )
    else:
        bullets.append(
            f"Thin sample ({n} games min) — projection still leans "
            f"{prior_pct}% toward the league prior."
        )

    # 2. λ build-up (skipped for ML / F5_ML which the operator reads as
    # a Skellam tail, not a sum).
    home_label = home_tricode or "home"
    away_label = away_tricode or "away"
    lam_h = float(proj.lam_home or 0.0)
    lam_a = float(proj.lam_away or 0.0)
    if proj.market.canonical in ("Total", "F5_Total", "Team_Total"):
        lam_total = lam_h + lam_a
        if lam_total > 0:
            bullets.append(
                f"λ {home_label} {lam_h:.2f} + λ {away_label} {lam_a:.2f} "
                f"→ total {lam_total:.2f}."
            )
    elif proj.market.canonical in ("ML", "F5_ML", "Run_Line"):
        if lam_h > 0 and lam_a > 0:
            bullets.append(
                f"Skellam over λ {home_label} {lam_h:.2f} − "
                f"λ {away_label} {lam_a:.2f}."
            )

    # 3. Edge framing
    if edge_pp is not None and market_prob is not None and 0.0 < market_prob < 1.0:
        sign = "+" if edge_pp >= 0 else ""
        bullets.append(
            f"Model {round(proj.model_prob * 100, 1)}% vs market "
            f"{round(market_prob * 100, 1)}% → {sign}{edge_pp:.1f}pp edge."
        )

    return bullets
