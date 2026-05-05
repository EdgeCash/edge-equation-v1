def estimate_usage(raw_usage: float, injury_adjustment: float = 1.0) -> float:
    """
    Usage rate adjusted for injuries / rotation changes.
    """
    return raw_usage * injury_adjustment
