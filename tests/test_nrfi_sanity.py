"""Tests for the ML-vs-baseline sanity report (Phase 2b)."""

from __future__ import annotations

import json

import pytest


def test_sanity_row_dataclass():
    from edge_equation.engines.nrfi.training.sanity import SanityRow
    r = SanityRow(label="ml", n_games=400, base_rate=0.535,
                   accuracy=0.62, brier=0.215, log_loss=0.63, roi_units=18.4)
    assert r.label == "ml"
    assert r.n_games == 400


def test_sanity_report_summary_contains_deltas():
    from edge_equation.engines.nrfi.training.sanity import SanityReport, SanityRow
    ml = SanityRow(label="ml", n_games=400, base_rate=0.535,
                    accuracy=0.62, brier=0.215, log_loss=0.63)
    base = SanityRow(label="poisson", n_games=400, base_rate=0.535,
                      accuracy=0.57, brier=0.234, log_loss=0.67)
    rep = SanityReport(
        ml=ml, baseline=base,
        accuracy_delta=ml.accuracy - base.accuracy,
        brier_delta=ml.brier - base.brier,
        log_loss_delta=ml.log_loss - base.log_loss,
    )
    s = rep.summary()
    assert "ML bundle" in s and "Poisson" in s
    assert "n games" in s and "400" in s
    # Deltas appear with sign.
    assert "+0.050" in s or "+0.0500" in s  # accuracy_delta
    assert "-0.019" in s or "-0.0190" in s  # brier_delta
    assert "Passed min-improvement gate" in s


def test_sanity_report_summary_includes_notes():
    from edge_equation.engines.nrfi.training.sanity import SanityReport, SanityRow
    ml = SanityRow(label="ml", n_games=0, base_rate=0.0,
                    accuracy=0.0, brier=0.0, log_loss=0.0)
    base = SanityRow(label="baseline", n_games=0, base_rate=0.0,
                      accuracy=0.0, brier=0.0, log_loss=0.0)
    rep = SanityReport(ml=ml, baseline=base)
    rep.notes.append("custom diagnostic note")
    s = rep.summary()
    assert "custom diagnostic note" in s


def test_compute_sanity_returns_baseline_only_when_bundle_missing(monkeypatch, tmp_path):
    """When the engine bridge can't load a trained model, the report
    should still produce a valid baseline-only result with a note
    explaining why ML metrics are zero."""
    pd = pytest.importorskip("pandas")
    from edge_equation.engines.nrfi.training import sanity
    from edge_equation.engines.nrfi.config import NRFIConfig

    # Fake corpus rows with feature_blobs containing poisson_p_nrfi.
    rows = []
    for i in range(20):
        rows.append({
            "game_pk": i + 1,
            "game_date": "2026-04-15",
            "feature_blob": json.dumps({"poisson_p_nrfi": 0.5 + 0.01 * (i % 5),
                                          "lambda_total": 1.10}),
            "first_inn_runs": 1 if i % 3 == 0 else 0,
            "nrfi": 1 if i % 2 == 0 else 0,
        })
    corpus_df = pd.DataFrame(rows)

    class _FakeStore:
        def __init__(self, *a, **kw): pass
        def query_df(self, sql, params=None):
            return corpus_df

    monkeypatch.setattr(sanity, "NRFIStore", _FakeStore)

    # Bridge claims no ML model loaded.
    class _FakeBridge:
        @staticmethod
        def available(): return False

    from edge_equation.engines.nrfi.integration import engine_bridge as eb
    monkeypatch.setattr(eb.NRFIEngineBridge, "try_load",
                          classmethod(lambda cls, cfg=None: _FakeBridge()))

    cfg = NRFIConfig(
        cache_dir=tmp_path / "cache",
        duckdb_path=tmp_path / "cache" / "nrfi.duckdb",
        model_dir=tmp_path / "models",
    )

    report = sanity.compute_sanity(season=2026, config=cfg)
    # Baseline gets real metrics; ML row is the degenerate empty.
    assert report.baseline.n_games == 20
    assert report.ml.label == "ml-not-loaded"
    assert any("baseline-only" in n for n in report.notes)


def test_compute_sanity_no_data_returns_empty_report(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    from edge_equation.engines.nrfi.training import sanity
    from edge_equation.engines.nrfi.config import NRFIConfig

    class _EmptyStore:
        def __init__(self, *a, **kw): pass
        def query_df(self, sql, params=None):
            return pd.DataFrame()

    monkeypatch.setattr(sanity, "NRFIStore", _EmptyStore)

    cfg = NRFIConfig(
        cache_dir=tmp_path / "cache",
        duckdb_path=tmp_path / "cache" / "nrfi.duckdb",
        model_dir=tmp_path / "models",
    )

    report = sanity.compute_sanity(season=2026, config=cfg)
    assert report.ml.n_games == 0
    assert report.baseline.n_games == 0
    assert any("no rows" in n for n in report.notes)


def test_passed_improvement_gate_logic():
    """Brier delta and log-loss delta must both clear the (negative)
    threshold to flip the gate to True."""
    from edge_equation.engines.nrfi.training.sanity import SanityReport, SanityRow

    ml = SanityRow(label="ml", n_games=400, base_rate=0.535,
                    accuracy=0.62, brier=0.215, log_loss=0.62)
    base = SanityRow(label="baseline", n_games=400, base_rate=0.535,
                      accuracy=0.57, brier=0.225, log_loss=0.65)
    rep = SanityReport(
        ml=ml, baseline=base,
        accuracy_delta=ml.accuracy - base.accuracy,
        brier_delta=ml.brier - base.brier,
        log_loss_delta=ml.log_loss - base.log_loss,
    )
    # brier_delta = -0.010, log_loss_delta = -0.030 — should pass with
    # default thresholds (0.005 / 0.01).
    rep.passed_min_improvement = (
        rep.brier_delta <= -0.005 and rep.log_loss_delta <= -0.01
    )
    assert rep.passed_min_improvement is True

    # Now flip ML to perform WORSE — must fail.
    ml_bad = SanityRow(label="ml", n_games=400, base_rate=0.535,
                        accuracy=0.55, brier=0.240, log_loss=0.70)
    rep2 = SanityReport(
        ml=ml_bad, baseline=base,
        brier_delta=ml_bad.brier - base.brier,
        log_loss_delta=ml_bad.log_loss - base.log_loss,
    )
    rep2.passed_min_improvement = (
        rep2.brier_delta <= -0.005 and rep2.log_loss_delta <= -0.01
    )
    assert rep2.passed_min_improvement is False
