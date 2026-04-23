"""
That K Report -- K-prop specific grade thresholds.

The main Edge Equation engine uses ConfidenceScorer's ML/Spread/Total
calibration (A+ at edge >= 8%, A at 5%, B at 3%, ...).  K-prop markets
are noisier and want a slightly different shape:

    A+ :  +10.0% or higher
    A  :  +7.0% to +9.9%
    A- :  +4.5% to +6.9%
    B  :  +2.0% to +4.4%
    C  :  -1.9% to +1.9%
    D  :  -2.0% to -5.9%
    F  :  -6.0% or worse

"Top Plays" := any row graded A- or higher.  That set drives both the
"Tonight's Top Plays" section on the Projections card AND the main
Season Ledger denominator on the Results card.  Everything else goes
in "Full Slate Projections" / "Full Slate Calibration" instead.

This grader is intentionally separate from ConfidenceScorer so the
K-Report brand can tune one ladder without disturbing the main
engine's calibration-history weightings.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Union


# Thresholds quoted verbatim from the brand brief.
K_A_PLUS = Decimal("0.100")
K_A = Decimal("0.070")
K_A_MINUS = Decimal("0.045")
K_B = Decimal("0.020")
K_C_HIGH = Decimal("0.019")     # +1.9% inclusive
K_C_LOW = Decimal("-0.019")     # -1.9% inclusive
K_D_LOW = Decimal("-0.059")     # -5.9% inclusive


# Grades that qualify for the "Top Plays" section + the main Season
# Ledger denominator.  A-minus is the floor; anything below drops to
# the Full Slate buckets.
TOP_PLAY_GRADES = frozenset({"A+", "A", "A-"})


def grade_k_edge(edge: Union[Decimal, float, int, str]) -> str:
    """Translate a probability-space edge magnitude to a letter grade.
    Accepts Decimal/float/int/str for caller convenience."""
    if edge is None:
        return "C"
    e = edge if isinstance(edge, Decimal) else Decimal(str(edge))
    if e >= K_A_PLUS:
        return "A+"
    if e >= K_A:
        return "A"
    if e >= K_A_MINUS:
        return "A-"
    if e >= K_B:
        return "B"
    if e >= K_C_LOW:
        return "C"
    if e >= K_D_LOW:
        return "D"
    return "F"


def is_top_play(grade: str) -> bool:
    """True iff the grade qualifies for Top Plays / Season Ledger."""
    return (grade or "") in TOP_PLAY_GRADES


def grade_rank(grade: str) -> int:
    """Integer ranking for sorting, high-first.  Unknown grades
    sort to the bottom (rank -1) so a malformed row never pushes
    above a real grade."""
    order = {"A+": 6, "A": 5, "A-": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    return order.get(grade or "", -1)
