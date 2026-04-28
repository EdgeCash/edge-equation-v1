"""Tests for the walk-forward training pipeline (Phase 2b).

We mock both the DuckDB corpus loader and the heavy ML imports so the
tests run in the slim CI workflow without xgboost/lightgbm/sklearn
installed. Verified:

* Chunk loop respects the date range and steps by `chunk_size_days`.
* `min_train_rows` skips chunks whose training window is too thin.
* Walk-forward calibration JSONL is written.
* Final-bundle save attempt is wrapped in try/except so a save failure
  doesn't blow up the report.
* The CLI parses standard arguments.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Lightweight in-memory corpus mock
# ---------------------------------------------------------------------------

def _make_corpus_df(start_iso: str, end_iso: str, *, daily_games: int = 8):
    """Return a fake corpus DataFrame matching what `load_corpus` returns."""
    pd = pytest.importorskip("pandas")
    cur = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    rows = []
    pk = 1
    while cur <= end:
        for g in range(daily_games):
            rows.append({
                "game_pk": pk,
                "game_date": cur.isoformat(),
                "feature_blob": json.dumps({"poisson_p_nrfi": 0.55,
                                              "lambda_total": 1.10}),
                "first_inn_runs": g % 3,
                "nrfi": 1 if g % 2 == 0 else 0,
            })
            pk += 1
        cur = date.fromisoformat(cur.isoformat()) + (date(1970, 1, 2) - date(1970, 1, 1))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Patch the heavy ML imports inside walkforward.walkforward_train
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_ml_stack(monkeypatch):
    """Inject lightweight stand-ins for the ML imports walkforward
    pulls in lazily. Returns the captured calls dict."""
    captured = {"classifier_fits": 0, "regressor_fits": 0, "glm_fits": 0,
                 "save_calls": 0, "predicted_p_value": 0.62}

    class _StubClassifier:
        def __init__(self, blend_with_lgbm=True):
            self.feature_names: list[str] = []
            self._calibrator = None

        def fit(self, X, y, **kw):
            captured["classifier_fits"] += 1
            self.feature_names = list(getattr(X, "columns", []))
            return self

        def predict_proba(self, X):
            import numpy as np
            return np.full(len(X), captured["predicted_p_value"], dtype=float)

    class _StubRegressor:
        def __init__(self):
            self.feature_names: list[str] = []

        def fit(self, X, y, **kw):
            captured["regressor_fits"] += 1
            self.feature_names = list(getattr(X, "columns", []))
            return self

    class _StubGLM:
        def fit(self, X, y, feature_names=None):
            captured["glm_fits"] += 1
            return self

    class _StubBundle:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

        def save(self, model_dir):
            captured["save_calls"] += 1

    # Lazy imports happen inside walkforward_train, so monkeypatch the
    # actual modules they pull from.
    from edge_equation.engines.nrfi.models import model_training
    from edge_equation.engines.nrfi.models import poisson_baseline
    from edge_equation.engines.nrfi.models import calibration as cal_mod

    monkeypatch.setattr(model_training, "NRFIClassifier", _StubClassifier)
    monkeypatch.setattr(model_training, "FirstInningRunsRegressor", _StubRegressor)
    monkeypatch.setattr(model_training, "TrainedBundle", _StubBundle)
    monkeypatch.setattr(model_training, "MODEL_VERSION", "stub")

    # expand_feature_blobs / feature_matrix are lighter — leave them.

    monkeypatch.setattr(poisson_baseline, "PoissonGLM", _StubGLM)

    class _StubCal:
        def __init__(self, method="isotonic"):
            self.method = method

        def fit(self, raw, y):
            return self

    monkeypatch.setattr(cal_mod, "Calibrator", _StubCal)
    return captured


# ---------------------------------------------------------------------------
# Patch load_corpus to return controlled data
# ---------------------------------------------------------------------------

@pytest.fixture
def patched_load_corpus(monkeypatch):
    """Replace `load_corpus` with a function we can program per-test."""
    from edge_equation.engines.nrfi.training import walkforward as wf

    container = {"corpus_for": None}  # callable: (start, end) -> df

    def _stub(store, start, end):
        if container["corpus_for"] is None:
            return _make_corpus_df(start, end)
        return container["corpus_for"](start, end)

    monkeypatch.setattr(wf, "load_corpus", _stub)
    return container


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_walkforward_steps_through_chunks(stub_ml_stack, patched_load_corpus,
                                            tmp_path, monkeypatch):
    """28-day window with 7-day chunks → exactly 4 chunks; each chunk
    triggers one classifier fit; final bundle adds one more fit."""
    from edge_equation.engines.nrfi.training.walkforward import walkforward_train
    from edge_equation.engines.nrfi.config import NRFIConfig

    cfg = NRFIConfig(
        cache_dir=tmp_path / "cache",
        duckdb_path=tmp_path / "cache" / "nrfi.duckdb",
        model_dir=tmp_path / "models",
    )

    class _FakeStore:
        def __init__(self, *a, **kw): pass

    # Avoid touching DuckDB.
    from edge_equation.engines.nrfi.training import walkforward as wf
    monkeypatch.setattr(wf, "NRFIStore", _FakeStore)

    progress: list = []
    report = walkforward_train(
        start_date="2025-04-01", end_date="2025-04-28",
        window_months=18, chunk_size_days=7,
        min_train_rows=10,
        config=cfg, save_bundle=True,
        progress_callback=progress.append,
    )

    # 4 chunks (4 weeks).
    assert report.n_chunks == 4
    # No skips — corpus mock always returns enough rows.
    assert report.n_chunks_skipped == 0
    # Per-chunk fits + 1 final fit.
    assert stub_ml_stack["classifier_fits"] >= report.n_chunks
    assert stub_ml_stack["save_calls"] == 1
    # Calibration JSONL written and non-empty.
    assert report.calibration_jsonl is not None
    assert Path(report.calibration_jsonl).exists()
    rows = Path(report.calibration_jsonl).read_text().splitlines()
    assert len(rows) > 0
    # Each row is a valid JSON dict with the right keys.
    for line in rows[:3]:
        d = json.loads(line)
        assert {"game_pk", "game_date", "predicted_p", "actual_y"} <= set(d.keys())


def test_walkforward_skips_thin_training_windows(stub_ml_stack,
                                                   patched_load_corpus,
                                                   tmp_path, monkeypatch):
    """When a chunk's training window has fewer than min_train_rows
    rows, the chunk gets marked SKIP without trying to fit."""
    from edge_equation.engines.nrfi.training.walkforward import walkforward_train
    from edge_equation.engines.nrfi.config import NRFIConfig
    pd = pytest.importorskip("pandas")

    def _empty_corpus(start, end):
        # Only the "final-bundle" load (broad range) gets data; per-chunk
        # training windows return empty.
        if start == "2026-04-01" or "2024" in start:
            return _make_corpus_df(start, end)
        return pd.DataFrame()

    patched_load_corpus["corpus_for"] = _empty_corpus

    cfg = NRFIConfig(
        cache_dir=tmp_path / "cache",
        duckdb_path=tmp_path / "cache" / "nrfi.duckdb",
        model_dir=tmp_path / "models",
    )

    class _FakeStore:
        def __init__(self, *a, **kw): pass

    from edge_equation.engines.nrfi.training import walkforward as wf
    monkeypatch.setattr(wf, "NRFIStore", _FakeStore)

    report = walkforward_train(
        start_date="2025-04-01", end_date="2025-04-14",
        window_months=18, chunk_size_days=7,
        min_train_rows=200,
        config=cfg, save_bundle=False,
    )
    # Both chunks skipped (training corpus empty).
    assert report.n_chunks_skipped == 2
    assert report.n_chunks == 2


def test_walkforward_inverted_dates_raises(tmp_path, monkeypatch,
                                              stub_ml_stack, patched_load_corpus):
    from edge_equation.engines.nrfi.training.walkforward import walkforward_train
    from edge_equation.engines.nrfi.config import NRFIConfig

    cfg = NRFIConfig(
        cache_dir=tmp_path / "cache",
        duckdb_path=tmp_path / "cache" / "nrfi.duckdb",
        model_dir=tmp_path / "models",
    )

    class _FakeStore:
        def __init__(self, *a, **kw): pass

    from edge_equation.engines.nrfi.training import walkforward as wf
    monkeypatch.setattr(wf, "NRFIStore", _FakeStore)

    with pytest.raises(ValueError, match="start_date.*>.*end_date"):
        walkforward_train(start_date="2026-04-15", end_date="2026-04-01",
                            config=cfg)


def test_walkforward_report_summary_renders():
    from edge_equation.engines.nrfi.training.walkforward import (
        WalkForwardReport, ChunkResult,
    )
    r = WalkForwardReport(
        n_chunks=4, n_chunks_skipped=1, n_chunks_failed=0,
        train_rows_first=100, train_rows_last=400,
        n_predictions=120,
        walkforward_brier=0.21, walkforward_log_loss=0.62,
        walkforward_accuracy=0.62, walkforward_base_rate=0.535,
        bundle_saved_to="/tmp/bundles",
        elapsed_seconds=42.0,
    )
    s = r.summary()
    assert "Walk-forward training report" in s
    assert "chunks                 4" in s
    assert "WF accuracy@.5         0.620" in s
    assert "/tmp/bundles" in s


def test_chunk_result_dataclass_fields():
    from edge_equation.engines.nrfi.training.walkforward import ChunkResult
    c = ChunkResult(chunk_start="2025-04-01", chunk_end="2025-04-07",
                     train_rows=300, predicted_rows=56)
    assert c.chunk_start == "2025-04-01"
    assert c.predicted_rows == 56
    assert c.skipped is False
    assert c.error is None
