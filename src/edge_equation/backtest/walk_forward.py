"""
Walk-forward cross-validation fold scheduler.

Two scheduling modes over a date range:
- expanding: train window grows, test window is fixed at test_days and slides
              forward by step_days each fold.
- rolling:   train window is fixed at train_days and slides forward by
              step_days each fold; test window is also test_days.

All dates are standard-library datetime.date. No lookahead: test_start is
always strictly after train_end.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional


@dataclass(frozen=True)
class WalkForwardFold:
    """One train/test split with inclusive date bounds."""
    fold_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    def to_dict(self) -> dict:
        return {
            "fold_id": self.fold_id,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
        }


class WalkForward:
    """
    Walk-forward split generator:
    - expanding(start, end, first_train_days, test_days, step_days=None)
    - rolling(start, end, train_days, test_days, step_days=None)
    Both return a list of WalkForwardFold in chronological order.
    """

    @staticmethod
    def _validate(
        start: date,
        end: date,
        train_days: int,
        test_days: int,
        step_days: int,
    ) -> None:
        if end <= start:
            raise ValueError(f"end ({end}) must be after start ({start})")
        if train_days <= 0:
            raise ValueError(f"train_days must be positive, got {train_days}")
        if test_days <= 0:
            raise ValueError(f"test_days must be positive, got {test_days}")
        if step_days <= 0:
            raise ValueError(f"step_days must be positive, got {step_days}")

    @staticmethod
    def expanding(
        start: date,
        end: date,
        first_train_days: int,
        test_days: int,
        step_days: Optional[int] = None,
    ) -> List[WalkForwardFold]:
        step = step_days if step_days is not None else test_days
        WalkForward._validate(start, end, first_train_days, test_days, step)

        folds: List[WalkForwardFold] = []
        fold_id = 0
        train_end = start + timedelta(days=first_train_days - 1)
        while True:
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=test_days - 1)
            if test_end > end:
                break
            folds.append(WalkForwardFold(
                fold_id=fold_id,
                train_start=start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            ))
            fold_id += 1
            train_end = train_end + timedelta(days=step)
        return folds

    @staticmethod
    def rolling(
        start: date,
        end: date,
        train_days: int,
        test_days: int,
        step_days: Optional[int] = None,
    ) -> List[WalkForwardFold]:
        step = step_days if step_days is not None else test_days
        WalkForward._validate(start, end, train_days, test_days, step)

        folds: List[WalkForwardFold] = []
        fold_id = 0
        train_start = start
        while True:
            train_end = train_start + timedelta(days=train_days - 1)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=test_days - 1)
            if test_end > end:
                break
            folds.append(WalkForwardFold(
                fold_id=fold_id,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            ))
            fold_id += 1
            train_start = train_start + timedelta(days=step)
        return folds
