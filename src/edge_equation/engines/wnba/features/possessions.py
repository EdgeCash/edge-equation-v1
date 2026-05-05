def estimate_possessions(pace: float, minutes: float = 40.0) -> float:
    """
    Convert pace (per 40 minutes) into expected possessions.
    """
    return pace * (minutes / 40.0)
