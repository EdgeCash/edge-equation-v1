def estimate_pace(team_pace: float, opp_pace: float) -> float:
    """
    Simple blended pace model.
    WNBA pace is stable and predictable, so a 50/50 blend works well.
    """
    return 0.5 * (team_pace + opp_pace)
