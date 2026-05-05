def apply_context(base_value: float, rest_days: int, travel_factor: float) -> float:
    """
    Apply simple context adjustments:
    - Rest days (positive)
    - Travel factor (negative)
    """
    rest_adj = 1.0 + (0.02 * rest_days)
    travel_adj = 1.0 - travel_factor
    return base_value * rest_adj * travel_adj
