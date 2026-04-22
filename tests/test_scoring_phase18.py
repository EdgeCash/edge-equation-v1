from decimal import Decimal
import pytest

from edge_equation.math.scoring import (
    A_PLUS_THRESHOLD,
    A_THRESHOLD,
    B_THRESHOLD,
    C_THRESHOLD,
    D_THRESHOLD,
    ConfidenceScorer,
    PICK_EDGE_FLOOR_ML,
    PICK_EDGE_FLOOR_PROP,
    PICK_EDGE_FLOOR_SPREAD,
    PICK_EDGE_FLOOR_TOTAL,
)


# ------------------------------------------------ grade()


def test_a_plus_threshold():
    assert ConfidenceScorer.grade(Decimal('0.08')) == "A+"
    assert ConfidenceScorer.grade(Decimal('0.10')) == "A+"


def test_a_threshold():
    assert ConfidenceScorer.grade(Decimal('0.05')) == "A"
    assert ConfidenceScorer.grade(Decimal('0.079')) == "A"


def test_b_threshold():
    assert ConfidenceScorer.grade(Decimal('0.03')) == "B"
    assert ConfidenceScorer.grade(Decimal('0.049')) == "B"


def test_c_threshold():
    assert ConfidenceScorer.grade(Decimal('0')) == "C"
    assert ConfidenceScorer.grade(Decimal('0.029')) == "C"


def test_d_threshold():
    assert ConfidenceScorer.grade(Decimal('-0.03')) == "D"
    assert ConfidenceScorer.grade(Decimal('-0.001')) == "D"


def test_f_threshold():
    assert ConfidenceScorer.grade(Decimal('-0.05')) == "F"
    assert ConfidenceScorer.grade(Decimal('-1')) == "F"


def test_grade_none_returns_c():
    assert ConfidenceScorer.grade(None) == "C"


def test_grade_accepts_float_or_str():
    assert ConfidenceScorer.grade(0.08) == "A+"
    assert ConfidenceScorer.grade("0.05") == "A"


def test_thresholds_strictly_ordered():
    assert A_PLUS_THRESHOLD > A_THRESHOLD > B_THRESHOLD > C_THRESHOLD > D_THRESHOLD


# ------------------------------------------------ realization bucket


def test_realization_covers_all_grades():
    for g in ("A+", "A", "B", "C", "D", "F"):
        r = ConfidenceScorer.realization_for_grade(g)
        assert 0 < r < 100


def test_realization_monotone_by_grade():
    order = ("A+", "A", "B", "C", "D", "F")
    values = [ConfidenceScorer.realization_for_grade(g) for g in order]
    for i in range(1, len(values)):
        assert values[i] <= values[i - 1]


# ------------------------------------------------ per-market PICK gate


def test_pick_floor_ml():
    assert ConfidenceScorer.pick_edge_floor("ML") == PICK_EDGE_FLOOR_ML
    assert ConfidenceScorer.pick_edge_floor("Run_Line") == PICK_EDGE_FLOOR_ML
    assert ConfidenceScorer.pick_edge_floor("Puck_Line") == PICK_EDGE_FLOOR_ML


def test_pick_floor_spread():
    assert ConfidenceScorer.pick_edge_floor("Spread") == PICK_EDGE_FLOOR_SPREAD


def test_pick_floor_total():
    assert ConfidenceScorer.pick_edge_floor("Total") == PICK_EDGE_FLOOR_TOTAL
    assert ConfidenceScorer.pick_edge_floor("Game_Total") == PICK_EDGE_FLOOR_TOTAL


def test_pick_floor_prop():
    for m in ("HR", "K", "Points", "Rebounds", "Assists", "SOG"):
        assert ConfidenceScorer.pick_edge_floor(m) == PICK_EDGE_FLOOR_PROP


def test_pick_floor_unknown_defaults_strict():
    # Unknown market -> conservative strictest (prop) floor.
    assert ConfidenceScorer.pick_edge_floor("UNKNOWN_MARKET") == PICK_EDGE_FLOOR_PROP


def test_passes_pick_threshold_ml():
    assert ConfidenceScorer.passes_pick_threshold(Decimal('0.03'), "ML") is True
    assert ConfidenceScorer.passes_pick_threshold(Decimal('0.029'), "ML") is False


def test_passes_pick_threshold_spread_tighter_than_ml():
    # An edge that passes ML shouldn't necessarily pass Spread.
    assert ConfidenceScorer.passes_pick_threshold(Decimal('0.035'), "ML") is True
    assert ConfidenceScorer.passes_pick_threshold(Decimal('0.035'), "Spread") is False


def test_passes_pick_threshold_prop_strictest():
    # Props require 5% edge.
    assert ConfidenceScorer.passes_pick_threshold(Decimal('0.05'), "K") is True
    assert ConfidenceScorer.passes_pick_threshold(Decimal('0.04'), "K") is False


def test_passes_pick_threshold_none_edge_always_false():
    assert ConfidenceScorer.passes_pick_threshold(None, "ML") is False
