from decimal import Decimal


# Edge-to-grade thresholds. Tightened in Phase 18 to push the quality bar up:
#
#   A+:   edge >= 0.08  (elite)
#   A :   edge >= 0.05
#   B :   edge >= 0.03
#   C :   edge >= 0.00  (flat to barely positive -- informational only)
#   D :   edge >= -0.03 (small negative expectation, still reported)
#   F :   edge <  -0.03 (clearly unfavorable)
#
# Per-market PICK thresholds: a pick is only promoted to "published" in
# premium / non-public mode when its edge clears the market-specific floor.
# These are INDEPENDENT of the letter grade; they govern visibility, not
# calibration.
#
#   ML      0.03
#   Spreads 0.04
#   Totals  0.03
#   Props   0.05

A_PLUS_THRESHOLD = Decimal('0.080')
A_THRESHOLD = Decimal('0.050')
B_THRESHOLD = Decimal('0.030')
C_THRESHOLD = Decimal('0.000')
D_THRESHOLD = Decimal('-0.030')


ML_MARKETS = frozenset({"ML", "Run_Line", "Puck_Line", "BTTS"})
SPREAD_MARKETS = frozenset({"Spread"})
TOTAL_MARKETS = frozenset({"Total", "Game_Total"})
PROP_MARKETS = frozenset({
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
})

PICK_EDGE_FLOOR_ML = Decimal('0.030')
PICK_EDGE_FLOOR_SPREAD = Decimal('0.040')
PICK_EDGE_FLOOR_TOTAL = Decimal('0.030')
PICK_EDGE_FLOOR_PROP = Decimal('0.050')


class ConfidenceScorer:
    """
    Grade + PICK-gate helpers:
    - grade(edge)                         -> 'A+' | 'A' | 'B' | 'C' | 'D' | 'F'
    - realization_for_grade(grade)        -> int (expected hit-rate bucket)
    - pick_edge_floor(market_type)        -> Decimal
    - passes_pick_threshold(edge, market) -> bool
    """

    @staticmethod
    def grade(edge) -> str:
        if edge is None:
            return "C"
        e = edge if isinstance(edge, Decimal) else Decimal(str(edge))
        if e >= A_PLUS_THRESHOLD:
            return "A+"
        if e >= A_THRESHOLD:
            return "A"
        if e >= B_THRESHOLD:
            return "B"
        if e >= C_THRESHOLD:
            return "C"
        if e >= D_THRESHOLD:
            return "D"
        return "F"

    @staticmethod
    def realization_for_grade(grade: str) -> int:
        if grade == "A+":
            return 68
        if grade == "A":
            return 59
        if grade == "B":
            return 52
        if grade == "C":
            return 47
        if grade == "D":
            return 42
        return 35  # F

    @staticmethod
    def pick_edge_floor(market_type: str) -> Decimal:
        if market_type in ML_MARKETS:
            return PICK_EDGE_FLOOR_ML
        if market_type in SPREAD_MARKETS:
            return PICK_EDGE_FLOOR_SPREAD
        if market_type in TOTAL_MARKETS:
            return PICK_EDGE_FLOOR_TOTAL
        if market_type in PROP_MARKETS:
            return PICK_EDGE_FLOOR_PROP
        # Unknown market -> conservative: default to the strictest (props).
        return PICK_EDGE_FLOOR_PROP

    @staticmethod
    def passes_pick_threshold(edge, market_type: str) -> bool:
        if edge is None:
            return False
        e = edge if isinstance(edge, Decimal) else Decimal(str(edge))
        return e >= ConfidenceScorer.pick_edge_floor(market_type)
