"""
Premium formatter.

Pure formatting from PremiumPick to a flat dict suitable for card payloads.
No I/O, no side effects.
"""
from edge_equation.premium.premium_pick import PremiumPick


def format_premium_pick(premium_pick: PremiumPick) -> dict:
    bp = premium_pick.base_pick
    return {
        "selection": bp.selection,
        "market_type": bp.market_type,
        "sport": bp.sport,
        "line": bp.line.to_dict(),
        "fair_prob": str(bp.fair_prob) if bp.fair_prob is not None else None,
        "expected_value": str(bp.expected_value) if bp.expected_value is not None else None,
        "edge": str(bp.edge) if bp.edge is not None else None,
        "grade": bp.grade,
        "kelly": str(bp.kelly) if bp.kelly is not None else None,
        "p10": str(premium_pick.p10) if premium_pick.p10 is not None else None,
        "p50": str(premium_pick.p50) if premium_pick.p50 is not None else None,
        "p90": str(premium_pick.p90) if premium_pick.p90 is not None else None,
        "mean": str(premium_pick.mean) if premium_pick.mean is not None else None,
        "notes": premium_pick.notes,
        "game_id": bp.game_id,
        "event_time": bp.event_time,
    }
