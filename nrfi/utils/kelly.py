"""Fractional Kelly stake sizing with vig haircut and safety caps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StakeRecommendation:
    edge: float           # decimal edge after vig buffer (e.g. 0.06 = +6pp)
    kelly_full: float     # full-Kelly stake fraction
    kelly_fraction: float # applied fraction (e.g. 0.25 = quarter Kelly)
    stake_units: float    # capped recommended stake
    reason: Optional[str] = None  # populated when bet is suppressed


def american_to_decimal(odds: float) -> float:
    """American odds → decimal odds. -110 → 1.9091."""
    if odds == 0:
        raise ValueError("American odds cannot be 0")
    return 1.0 + (100.0 / abs(odds)) if odds < 0 else 1.0 + (odds / 100.0)


def implied_probability(american_odds: float) -> float:
    """American odds → implied (vigged) probability in [0,1]."""
    dec = american_to_decimal(american_odds)
    return 1.0 / dec


def kelly_stake(
    model_prob: float,
    market_prob: float,
    american_odds: float = -110.0,
    *,
    fraction: float = 0.25,
    min_edge: float = 0.04,
    vig_buffer: float = 0.02,
    max_stake_units: float = 2.0,
) -> StakeRecommendation:
    """Return a Kelly-sized stake recommendation.

    Parameters
    ----------
    model_prob : Calibrated NRFI probability from the engine, in [0,1].
    market_prob : Implied probability of the same outcome from the
        sportsbook (already converted from posted odds — pass straight
        from `implied_probability`). The vig buffer is applied here.
    american_odds : The price being offered for that outcome.
    fraction : Multiplier applied to full Kelly (1.0 = full, 0.25 = quarter).
    min_edge : Minimum |model − market_after_buffer| required to bet.
    vig_buffer : Subtract this from `market_prob` to be conservative
        about juice / closing line value.
    max_stake_units : Hard cap on units returned (treats 1u as 1% of bankroll).
    """
    p = max(0.0, min(1.0, float(model_prob)))
    q = max(0.0, min(1.0, float(market_prob) - vig_buffer))

    edge = p - q
    dec = american_to_decimal(american_odds)
    b = dec - 1.0  # net decimal odds

    # Full Kelly: f* = (bp - (1-p)) / b
    if b <= 0:
        return StakeRecommendation(edge, 0.0, fraction, 0.0, "non-positive odds")
    full = (b * p - (1.0 - p)) / b

    if edge < min_edge or full <= 0:
        return StakeRecommendation(
            edge, full, fraction, 0.0,
            f"edge {edge*100:.2f}pp < min {min_edge*100:.2f}pp" if edge < min_edge
            else "negative Kelly",
        )

    stake = max(0.0, min(max_stake_units, full * fraction * 100.0))
    return StakeRecommendation(edge, full, fraction, stake, None)
