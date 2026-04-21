import pytest
from decimal import Decimal

from edge_equation.backtest.grading import (
    GradeCalibrator,
    GradeThresholds,
    DEFAULT_A_QUANTILE,
    DEFAULT_B_QUANTILE,
    DEFAULT_C_QUANTILE,
    DEFAULT_D_QUANTILE,
)


def test_fit_returns_thresholds_type():
    t = GradeCalibrator.fit([0.01, 0.02, 0.03, 0.04, 0.05])
    assert isinstance(t, GradeThresholds)
    assert t.n_fit == 5
    assert t.a_plus is None


def test_fit_thresholds_ordered():
    edges = [i * 0.001 for i in range(100)]
    t = GradeCalibrator.fit(edges)
    assert t.a > t.b > t.c > t.d


def test_fit_with_a_plus_quantile():
    edges = [i * 0.001 for i in range(100)]
    t = GradeCalibrator.fit(edges, a_plus_quantile=Decimal('0.95'))
    assert t.a_plus is not None
    assert t.a_plus > t.a


def test_fit_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        GradeCalibrator.fit([])


def test_fit_a_plus_quantile_too_low_raises():
    with pytest.raises(ValueError, match="a_plus_quantile"):
        GradeCalibrator.fit([0.01, 0.02], a_plus_quantile=Decimal('0.5'))


def test_fit_a_plus_quantile_one_raises():
    with pytest.raises(ValueError, match="a_plus_quantile"):
        GradeCalibrator.fit([0.01, 0.02], a_plus_quantile=Decimal('1'))


def test_grade_above_a_returns_a():
    t = GradeThresholds(
        a_plus=None,
        a=Decimal('0.05'),
        b=Decimal('0.03'),
        c=Decimal('0.01'),
        d=Decimal('0.00'),
        n_fit=100,
    )
    assert GradeCalibrator.grade(Decimal('0.05'), t) == "A"
    assert GradeCalibrator.grade(Decimal('0.10'), t) == "A"


def test_grade_tiers():
    t = GradeThresholds(
        a_plus=None,
        a=Decimal('0.05'),
        b=Decimal('0.03'),
        c=Decimal('0.01'),
        d=Decimal('0.00'),
        n_fit=100,
    )
    assert GradeCalibrator.grade(Decimal('0.04'), t) == "B"
    assert GradeCalibrator.grade(Decimal('0.02'), t) == "C"
    assert GradeCalibrator.grade(Decimal('0.005'), t) == "D"
    assert GradeCalibrator.grade(Decimal('-0.01'), t) == "F"


def test_grade_a_plus_when_set():
    t = GradeThresholds(
        a_plus=Decimal('0.08'),
        a=Decimal('0.05'),
        b=Decimal('0.03'),
        c=Decimal('0.01'),
        d=Decimal('0.00'),
        n_fit=100,
    )
    assert GradeCalibrator.grade(Decimal('0.09'), t) == "A+"
    assert GradeCalibrator.grade(Decimal('0.05'), t) == "A"


def test_grade_exactly_at_threshold_assigns_higher_tier():
    t = GradeThresholds(
        a_plus=None,
        a=Decimal('0.05'),
        b=Decimal('0.03'),
        c=Decimal('0.01'),
        d=Decimal('0.00'),
        n_fit=100,
    )
    assert GradeCalibrator.grade(Decimal('0.05'), t) == "A"
    assert GradeCalibrator.grade(Decimal('0.03'), t) == "B"


def test_fitted_thresholds_match_expected_quantiles():
    edges = [i * 0.001 for i in range(101)]  # 0.000..0.100 inclusive
    t = GradeCalibrator.fit(edges)
    # For a uniformly-spaced 101-element list, quantile q gives 0.1 * q
    assert t.a == Decimal('0.080000')  # 0.80 * 100 * 0.001
    assert t.b == Decimal('0.060000')
    assert t.c == Decimal('0.040000')
    assert t.d == Decimal('0.020000')


def test_grade_thresholds_frozen():
    t = GradeThresholds(
        a_plus=None,
        a=Decimal('0.05'),
        b=Decimal('0.03'),
        c=Decimal('0.01'),
        d=Decimal('0.00'),
        n_fit=100,
    )
    with pytest.raises(Exception):
        t.a = Decimal('0.99')


def test_grade_distribution_on_fitted_edges():
    # On the fitted set, roughly 20% should be A, 20% B, etc.
    import random
    random.seed(0)
    edges = sorted([random.gauss(0.02, 0.02) for _ in range(1000)])
    t = GradeCalibrator.fit(edges)
    grades = [GradeCalibrator.grade(Decimal(str(e)), t) for e in edges]
    # A should be ~200 (top 20%)
    assert 150 < grades.count("A") < 250
    assert 100 < grades.count("F") < 250


def test_thresholds_to_dict_has_strings():
    t = GradeCalibrator.fit([0.01, 0.02, 0.03], a_plus_quantile=Decimal('0.90'))
    d = t.to_dict()
    assert isinstance(d["a"], str)
    assert isinstance(d["a_plus"], str)
    assert d["n_fit"] == 3


def test_thresholds_to_dict_null_a_plus():
    t = GradeCalibrator.fit([0.01, 0.02, 0.03])
    d = t.to_dict()
    assert d["a_plus"] is None
