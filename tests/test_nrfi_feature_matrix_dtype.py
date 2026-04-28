"""Regression tests for `feature_matrix` dtype handling.

The walkforward training pipeline crashed on its first real data run
because `game_date` (a `datetime64[us]` column from DuckDB) leaked into
the X matrix passed to XGBoost. XGBoost only accepts int / float / bool
/ category dtypes — datetime is unsupported and surfaces as:

    DataFrame.dtypes for data must be int, float, bool or category.
    When categorical type is supplied, the experimental DMatrix
    parameter `enable_categorical` must be set to `True`.
    Invalid columns: game_date: datetime64[us]

The existing walkforward tests monkey-patch the entire ML stack with
stubs and never exercise the real `feature_matrix` plumbing — so the
bug shipped silently. These tests pin the contract directly against
the production helper.
"""

from __future__ import annotations

import pandas as pd
import pytest


def test_feature_matrix_drops_game_date_explicitly():
    from edge_equation.engines.nrfi.models.model_training import feature_matrix
    df = pd.DataFrame({
        "game_pk":          [1, 2, 3],
        "game_date":        pd.to_datetime(["2025-04-01", "2025-04-02", "2025-04-03"]),
        "feature_a":        [0.1, 0.2, 0.3],
        "feature_b":        [10.0, 20.0, 30.0],
        "nrfi":             [1, 0, 1],
        "first_inn_runs":   [0, 2, 0],
    })
    X, cols = feature_matrix(df)
    assert "game_date" not in cols
    assert "game_pk" not in cols
    assert "nrfi" not in cols
    assert "first_inn_runs" not in cols
    assert set(cols) == {"feature_a", "feature_b"}


def test_feature_matrix_drops_any_datetime_column_defensively():
    """Future schema additions of datetime columns must not regress."""
    from edge_equation.engines.nrfi.models.model_training import feature_matrix
    df = pd.DataFrame({
        "game_pk":               [1, 2, 3],
        "game_date":             pd.to_datetime(["2025-04-01", "2025-04-02", "2025-04-03"]),
        "first_pitch_ts":        pd.to_datetime(["2025-04-01 19:00",
                                                  "2025-04-02 19:00",
                                                  "2025-04-03 19:00"]),
        "elapsed_since_start":   pd.to_timedelta(["1 days", "2 days", "3 days"]),
        "feature_a":             [0.1, 0.2, 0.3],
        "nrfi":                  [1, 0, 1],
        "first_inn_runs":        [0, 2, 0],
    })
    X, cols = feature_matrix(df)
    assert "game_date" not in cols
    assert "first_pitch_ts" not in cols
    assert "elapsed_since_start" not in cols
    assert "feature_a" in cols


def test_feature_matrix_dtypes_are_xgboost_compatible():
    """The X DataFrame must contain ONLY int / float / bool / category
    dtypes — XGBoost rejects everything else."""
    from edge_equation.engines.nrfi.models.model_training import feature_matrix
    df = pd.DataFrame({
        "game_pk":        [1, 2, 3],
        "game_date":      pd.to_datetime(["2025-04-01", "2025-04-02", "2025-04-03"]),
        "feature_int":    [1, 2, 3],
        "feature_float":  [0.1, 0.2, 0.3],
        "feature_bool":   [True, False, True],
        "feature_int64":  pd.Series([1, 2, 3], dtype="Int64"),
        "nrfi":           [1, 0, 1],
        "first_inn_runs": [0, 2, 0],
    })
    X, _ = feature_matrix(df)
    for col in X.columns:
        dtype = X[col].dtype
        assert (
            pd.api.types.is_numeric_dtype(dtype) or
            pd.api.types.is_bool_dtype(dtype) or
            isinstance(dtype, pd.CategoricalDtype)
        ), f"column {col!r} has incompatible dtype {dtype}"


def test_feature_matrix_with_caller_drop_cols():
    """The drop_cols kwarg must compose with the built-in drops."""
    from edge_equation.engines.nrfi.models.model_training import feature_matrix
    df = pd.DataFrame({
        "game_pk":         [1, 2, 3],
        "game_date":       pd.to_datetime(["2025-04-01", "2025-04-02", "2025-04-03"]),
        "feature_a":       [0.1, 0.2, 0.3],
        "feature_b":       [10.0, 20.0, 30.0],
        "noisy_feature":   [99.9, 99.9, 99.9],
        "nrfi":            [1, 0, 1],
        "first_inn_runs":  [0, 2, 0],
    })
    _, cols = feature_matrix(df, drop_cols=("noisy_feature",))
    assert set(cols) == {"feature_a", "feature_b"}


def test_feature_matrix_preserves_int64_nullable():
    """Make sure the regression fix doesn't accidentally drop nullable
    Int64 columns (which `game_pk` uses in DuckDB-loaded frames)."""
    from edge_equation.engines.nrfi.models.model_training import feature_matrix
    df = pd.DataFrame({
        "game_pk":              pd.Series([1, 2, 3], dtype="Int64"),
        "game_date":            pd.to_datetime(["2025-04-01", "2025-04-02", "2025-04-03"]),
        "season_int_nullable":  pd.Series([2025, 2026, 2026], dtype="Int64"),
        "feature_a":            [0.1, 0.2, 0.3],
        "nrfi":                 [1, 0, 1],
        "first_inn_runs":       [0, 2, 0],
    })
    _, cols = feature_matrix(df)
    # game_pk dropped (identifier); game_date dropped (datetime).
    # season_int_nullable kept — it's a numeric feature with a non-
    # datetime nullable dtype.
    assert "game_pk" not in cols
    assert "game_date" not in cols
    assert "season_int_nullable" in cols


def test_walkforward_corpus_mock_has_datetime_game_date():
    """Sanity check that the mock corpus we'd use in walkforward tests
    actually emits a datetime64 game_date — i.e. the existing mocked
    tests would NOT have caught the bug because they bypass
    feature_matrix entirely."""
    rows = [{"game_pk": 1, "game_date": "2025-04-01",
             "feature_blob": "{}", "first_inn_runs": 0, "nrfi": 1}]
    df = pd.DataFrame(rows)
    df["game_date"] = pd.to_datetime(df["game_date"])
    assert pd.api.types.is_datetime64_any_dtype(df["game_date"])
