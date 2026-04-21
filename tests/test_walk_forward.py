import pytest
from datetime import date, timedelta

from edge_equation.backtest.walk_forward import WalkForward, WalkForwardFold


def test_expanding_basic_fold_structure():
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        first_train_days=10,
        test_days=5,
        step_days=5,
    )
    assert len(folds) > 0
    assert folds[0].train_start == date(2026, 1, 1)
    assert folds[0].train_end == date(2026, 1, 10)
    assert folds[0].test_start == date(2026, 1, 11)
    assert folds[0].test_end == date(2026, 1, 15)


def test_expanding_train_start_constant():
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=date(2026, 2, 28),
        first_train_days=10,
        test_days=5,
    )
    for f in folds:
        assert f.train_start == date(2026, 1, 1)


def test_expanding_train_end_grows():
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=date(2026, 2, 28),
        first_train_days=10,
        test_days=5,
    )
    assert len(folds) >= 2
    for i in range(1, len(folds)):
        assert folds[i].train_end > folds[i - 1].train_end


def test_expanding_no_overlap_between_train_and_test():
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=date(2026, 3, 1),
        first_train_days=20,
        test_days=7,
    )
    for f in folds:
        assert f.test_start == f.train_end + timedelta(days=1)


def test_rolling_train_window_fixed_length():
    train_days = 14
    folds = WalkForward.rolling(
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
        train_days=train_days,
        test_days=7,
    )
    for f in folds:
        assert (f.train_end - f.train_start).days == train_days - 1


def test_rolling_train_start_advances():
    folds = WalkForward.rolling(
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
        train_days=14,
        test_days=7,
    )
    assert len(folds) >= 2
    for i in range(1, len(folds)):
        assert folds[i].train_start > folds[i - 1].train_start


def test_rolling_step_defaults_to_test_days():
    folds = WalkForward.rolling(
        start=date(2026, 1, 1),
        end=date(2026, 3, 1),
        train_days=14,
        test_days=7,
    )
    for i in range(1, len(folds)):
        delta = (folds[i].train_start - folds[i - 1].train_start).days
        assert delta == 7


def test_rolling_explicit_step():
    folds = WalkForward.rolling(
        start=date(2026, 1, 1),
        end=date(2026, 4, 1),
        train_days=14,
        test_days=7,
        step_days=14,
    )
    for i in range(1, len(folds)):
        delta = (folds[i].train_start - folds[i - 1].train_start).days
        assert delta == 14


def test_folds_have_monotonic_fold_ids():
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=date(2026, 3, 1),
        first_train_days=10,
        test_days=7,
    )
    ids = [f.fold_id for f in folds]
    assert ids == list(range(len(folds)))


def test_test_end_never_exceeds_end():
    end = date(2026, 2, 10)
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=end,
        first_train_days=10,
        test_days=5,
    )
    for f in folds:
        assert f.test_end <= end


def test_insufficient_range_yields_empty_list():
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=date(2026, 1, 5),
        first_train_days=20,
        test_days=5,
    )
    assert folds == []


def test_invalid_end_before_start_raises():
    with pytest.raises(ValueError, match="must be after"):
        WalkForward.expanding(
            start=date(2026, 2, 1),
            end=date(2026, 1, 1),
            first_train_days=10,
            test_days=5,
        )


def test_invalid_zero_train_days_raises():
    with pytest.raises(ValueError, match="train_days"):
        WalkForward.rolling(
            start=date(2026, 1, 1),
            end=date(2026, 3, 1),
            train_days=0,
            test_days=5,
        )


def test_invalid_zero_test_days_raises():
    with pytest.raises(ValueError, match="test_days"):
        WalkForward.expanding(
            start=date(2026, 1, 1),
            end=date(2026, 3, 1),
            first_train_days=10,
            test_days=0,
        )


def test_fold_frozen():
    folds = WalkForward.expanding(
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        first_train_days=10,
        test_days=5,
    )
    with pytest.raises(Exception):
        folds[0].fold_id = 999


def test_fold_to_dict_iso_dates():
    folds = WalkForward.rolling(
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        train_days=10,
        test_days=5,
    )
    d = folds[0].to_dict()
    assert d["train_start"] == "2026-01-01"
    assert d["fold_id"] == 0
