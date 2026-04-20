from decimal import Decimal, getcontext

getcontext().prec = 28


class DeterministicStats:
    LAMBDA_DECAY = Decimal('0.95')
    MAX_UNIVERSAL_PROB_IMPACT = Decimal('0.10')
    MIN_PROP_MULTIPLIER = Decimal('0.75')
    MAX_PROP_MULTIPLIER = Decimal('1.25')

    UNIVERSAL_KEYS = [
        'pace_delta', 'off_eff_delta', 'def_eff_delta', 'sos_off_delta', 'sos_def_delta',
        'regression_off', 'regression_def', 'regression_form', 'volatility_team',
        'volatility_matchup', 'rest_delta', 'travel_delta', 'schedule_density',
        'home_edge', 'market_line_delta', 'form_off', 'form_def', 'form_combined',
        'matchup_strength', 'matchup_exploit', 'matchup_risk'
    ]

    @staticmethod
    def exponential_decay_rolling(values: list[float]) -> Decimal:
        if not values:
            return Decimal('0')
        n = len(values)
        weights = [DeterministicStats.LAMBDA_DECAY ** Decimal(i) for i in range(n)]
        weighted_sum = sum(Decimal(str(v)) * w for v, w in zip(reversed(values), weights))
        weight_sum = sum(weights)
        return (weighted_sum / weight_sum).quantize(Decimal('0.000001'))

    @staticmethod
    def compute_universal_sum(features: dict) -> Decimal:
        total = Decimal('0')
        for key in DeterministicStats.UNIVERSAL_KEYS:
            total += Decimal(str(features.get(key, 0.0)))
        return total

    @staticmethod
    def clamp_universal_prob(raw_universal_sum: Decimal) -> Decimal:
        if raw_universal_sum > DeterministicStats.MAX_UNIVERSAL_PROB_IMPACT:
            return DeterministicStats.MAX_UNIVERSAL_PROB_IMPACT
        if raw_universal_sum < -DeterministicStats.MAX_UNIVERSAL_PROB_IMPACT:
            return -DeterministicStats.MAX_UNIVERSAL_PROB_IMPACT
        return raw_universal_sum

    @staticmethod
    def clamp_prop_multiplier(raw_universal_sum: Decimal) -> Decimal:
        multiplier = Decimal('1') + raw_universal_sum
        if multiplier > DeterministicStats.MAX_PROP_MULTIPLIER:
            return DeterministicStats.MAX_PROP_MULTIPLIER
        if multiplier < DeterministicStats.MIN_PROP_MULTIPLIER:
            return DeterministicStats.MIN_PROP_MULTIPLIER
        return multiplier
