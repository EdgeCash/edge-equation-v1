"""Odds utilities matching Phase-2 EVCalculator conventions."""
from decimal import Decimal, ROUND_HALF_UP

from edge_equation.math.ev import EVCalculator


def american_to_implied_prob(odds: int) -> Decimal:
    dec_odds = EVCalculator.american_to_decimal(odds)
    return (Decimal('1') / dec_odds).quantize(Decimal('0.000001'))


def implied_prob_to_american(prob: Decimal) -> int:
    if not isinstance(prob, Decimal):
        prob = Decimal(str(prob))
    if prob <= Decimal('0') or prob >= Decimal('1'):
        raise ValueError(f"prob must be in (0, 1), got {prob}")
    if prob >= Decimal('0.5'):
        val = -(prob * Decimal('100')) / (Decimal('1') - prob)
    else:
        val = ((Decimal('1') - prob) * Decimal('100')) / prob
    return int(val.to_integral_value(rounding=ROUND_HALF_UP))
