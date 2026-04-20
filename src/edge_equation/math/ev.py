from decimal import Decimal


class EVCalculator:
    """
    Edge and standard fractional Kelly:
    - edge = fair_prob - implied_prob
    - kelly_full = edge / (decimal_odds - 1)
    - kelly_half = kelly_full / 2
    """

    @staticmethod
    def american_to_decimal(odds: int) -> Decimal:
        if odds > 0:
            return Decimal('1') + Decimal(odds) / Decimal('100')
        return Decimal('1') + Decimal('100') / Decimal(-odds)

    @staticmethod
    def calculate_edge(fair_prob: Decimal, american_odds: int) -> Decimal:
        dec_odds = EVCalculator.american_to_decimal(american_odds)
        implied = Decimal('1') / dec_odds
        edge = fair_prob - implied
        return edge.quantize(Decimal('0.000001'))

    @staticmethod
    def kelly_fraction(edge: Decimal, dec_odds: Decimal) -> Decimal:
        if dec_odds <= Decimal('1'):
            return Decimal('0')
        kelly = edge / (dec_odds - Decimal('1'))
        if kelly < Decimal('0'):
            return Decimal('0')
        return kelly.quantize(Decimal('0.0001'))

    @staticmethod
    def calibrate(public_mode: bool, fair_value: dict, line: dict):
        if public_mode:
            return {"edge": None, "kelly": None}
        fair_prob = fair_value.get("fair_prob", None)
        if fair_prob is None:
            return {"edge": None, "kelly": None}
        odds = int(line.get("odds", -110))
        edge = EVCalculator.calculate_edge(fair_prob, odds)
        # Only compute Kelly if edge >= 0.010 (B grade minimum)
        if edge < Decimal('0.010000'):
            return {"edge": edge, "kelly": Decimal('0')}
        dec_odds = EVCalculator.american_to_decimal(odds)
        kelly_full = EVCalculator.kelly_fraction(edge, dec_odds)
        kelly_half = (kelly_full / Decimal('2')).quantize(Decimal('0.0001'))
        # Cap at 25% of bankroll
        if kelly_half > Decimal('0.25'):
            kelly_half = Decimal('0.25')
        return {"edge": edge, "kelly": kelly_half}
