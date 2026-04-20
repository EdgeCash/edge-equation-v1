from decimal import Decimal


class ConfidenceScorer:
    """
    Grade by edge:
    - A+: edge > 0.050
    - A:  edge > 0.030
    - B:  edge > 0.010
    - C:  otherwise
    """

    @staticmethod
    def grade(edge: Decimal) -> str:
        if edge is None:
            return "C"
        if edge > Decimal('0.050'):
            return "A+"
        if edge > Decimal('0.030'):
            return "A"
        if edge > Decimal('0.010'):
            return "B"
        return "C"

    @staticmethod
    def realization_for_grade(grade: str) -> int:
        if grade == "A+":
            return 68
        if grade == "A":
            return 59
        if grade == "B":
            return 52
        return 47
