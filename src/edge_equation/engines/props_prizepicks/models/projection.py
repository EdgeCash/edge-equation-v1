"""Minimal projection layer for MLB player props.

This is intentionally conservative: until we promote a real Statcast player
projection model, the baseline probability starts at the market-implied price.
Callers may pass external projection probabilities to compute true edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from edge_equation.engines.tiering import Tier, classify_tier
from edge_equation.utils.kelly import implied_probability

from ..source.odds_api import PropMarketQuote


@dataclass(frozen=True)
class PropProjection:
    player_name: str
    bookmaker: str
    market_key: str
    selection: str
    line: Optional[float]
    american_odds: float
    market_prob: float
    model_prob: float
    tier: Tier

    @property
    def edge(self) -> float:
        return self.model_prob - self.market_prob


def project_from_quote(
    quote: PropMarketQuote,
    *,
    model_prob: Optional[float] = None,
    vig_buffer: float = 0.02,
) -> PropProjection:
    """Build one prop projection row from a normalized Odds API quote.

    If ``model_prob`` is omitted, we return the no-edge market baseline. That is
    useful for plumbing and display without inventing projections.
    """
    odds = float(quote.american_odds)
    market_prob = implied_probability(odds)
    p_model = float(model_prob) if model_prob is not None else market_prob
    edge = p_model - max(0.0, market_prob - vig_buffer)
    tier = classify_tier(market_type=quote.market_key, edge=edge).tier
    return PropProjection(
        player_name=quote.player_name,
        bookmaker=quote.bookmaker,
        market_key=quote.market_key,
        selection=quote.side,
        line=_float_or_none(quote.line),
        american_odds=odds,
        market_prob=market_prob,
        model_prob=p_model,
        tier=tier,
    )


def _float_or_none(value) -> Optional[float]:
    if value is None:
        return None
    return float(value)


__all__ = ["PropProjection", "project_from_quote"]
