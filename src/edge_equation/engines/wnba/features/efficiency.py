def estimate_efficiency(ppp: float, defense_adj: float) -> float:
    """
    Offensive efficiency adjusted by opponent defense.
    """
    return ppp * defense_adj
