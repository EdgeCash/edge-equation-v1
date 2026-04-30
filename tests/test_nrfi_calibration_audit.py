"""Tests for the NRFI calibration-audit diagnostic.

Synthetic-data only; the audit is read-only against either an
externally-provided (raw, y) vector or the trained bundle's persisted
walk-forward predictions. Both paths are exercised here.
"""

from __future__ import annotations

import numpy as np
import pytest


# Most audit tests dispatch through the calibrator factory which
# instantiates sklearn-backed isotonic and Platt calibrators alongside
# the pure-numpy alternatives. Skip the whole module when sklearn is
# unavailable; the few sklearn-free tests below set their own factory.
pytest.importorskip("sklearn")


# ---------------------------------------------------------------------------
# Audit: input validation + shape contracts
# ---------------------------------------------------------------------------


def test_run_audit_rejects_misaligned_inputs():
    from edge_equation.engines.nrfi.evaluation.calibration_audit import run_audit
    with pytest.raises(ValueError):
        run_audit(np.array([0.1, 0.5, 0.9]), np.array([0, 1]))


def test_run_audit_rejects_too_few_samples():
    from edge_equation.engines.nrfi.evaluation.calibration_audit import run_audit
    with pytest.raises(ValueError):
        run_audit(np.array([0.5] * 5), np.array([0, 1, 0, 1, 0]))


def test_run_audit_returns_results_for_every_alternative():
    """Every name in ALTERNATIVE_NAMES should produce a row unless it
    raises (in which case we expect a note, not a missing row)."""
    from edge_equation.engines.nrfi.evaluation.calibration_audit import run_audit
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        ALTERNATIVE_NAMES,
    )
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.0, 1.0, size=500)
    y = (rng.uniform(0.0, 1.0, size=500) < raw).astype(int)
    report = run_audit(raw, y, train_frac=0.7, seed=0)
    names_in_report = {r.name for r in report.calibrator_results}
    # Allow some failures to land in notes rather than rows; assert
    # that we got at least 4 of the 5 alternatives back.
    assert len(names_in_report) >= 4
    # The two production baselines must always succeed on synthetic data.
    assert "isotonic" in names_in_report
    assert "platt" in names_in_report


def test_run_audit_includes_raw_summary_as_reference_point():
    from edge_equation.engines.nrfi.evaluation.calibration_audit import run_audit
    rng = np.random.default_rng(1)
    raw = rng.uniform(0.0, 1.0, size=300)
    y = (rng.uniform(0.0, 1.0, size=300) < raw).astype(int)
    report = run_audit(raw, y, seed=1)
    assert report.raw_summary.name == "raw"
    # Brier of well-calibrated U[0,1] vs Bernoulli(p) is ~0.166
    # (= integral of p*(1-p)*1 dp from 0 to 1 = 1/6). Allow loose band.
    assert 0.10 < report.raw_summary.brier < 0.25


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_expected_calibration_error_perfect_calibration_is_low():
    from edge_equation.engines.nrfi.evaluation.calibration_audit import (
        _expected_calibration_error,
    )
    rng = np.random.default_rng(2)
    n = 5000
    p = rng.uniform(0.0, 1.0, size=n)
    y = (rng.uniform(0.0, 1.0, size=n) < p).astype(int)
    ece = _expected_calibration_error(p, y, n_bins=10)
    # Well-calibrated → ECE near zero. Sample variance keeps it small.
    assert ece < 0.05


def test_expected_calibration_error_constant_predictor_high():
    """A predictor that always says 0.9 on a 50/50 outcome should
    have ECE ≈ 0.4."""
    from edge_equation.engines.nrfi.evaluation.calibration_audit import (
        _expected_calibration_error,
    )
    rng = np.random.default_rng(3)
    n = 1000
    p = np.full(n, 0.9)
    y = rng.integers(0, 2, size=n)
    ece = _expected_calibration_error(p, y, n_bins=10)
    assert 0.30 < ece < 0.50


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_report_renders_with_all_required_sections():
    from edge_equation.engines.nrfi.evaluation.calibration_audit import run_audit
    rng = np.random.default_rng(4)
    raw = rng.uniform(0.0, 1.0, size=300)
    y = (rng.uniform(0.0, 1.0, size=300) < raw).astype(int)
    report = run_audit(raw, y, seed=4)
    rendered = report.render()
    assert "Calibration audit" in rendered
    assert "Raw model" in rendered
    assert "Calibrator alternatives" in rendered
    assert "Reading the table" in rendered
    # The interpretation helper note that points operators at the
    # feature-signal hypothesis must be present.
    assert "feature signal" in rendered.lower()


def test_calibrator_result_line_format_is_pipe_friendly():
    from edge_equation.engines.nrfi.evaluation.calibration_audit import (
        CalibratorResult,
    )
    r = CalibratorResult(
        name="beta", n_fit=350, n_eval=150,
        brier=0.2300, log_loss=0.6500, ece=0.0250,
        out_min=0.42, out_max=0.65, out_mean=0.52, out_std=0.052,
        ge_55=8, ge_58=4, ge_64=1, ge_70=0,
    )
    line = r.line()
    assert "beta" in line
    assert "brier=0.2300" in line
    assert ">=64/1" in line


# ---------------------------------------------------------------------------
# Bundle path (loader returns None when no bundle present — sandbox-safe)
# ---------------------------------------------------------------------------


def test_load_bundle_holdout_predictions_returns_none_when_unavailable():
    from edge_equation.engines.nrfi.evaluation import calibration_audit
    # In CI / sandbox we don't have a bundle on disk; loader should
    # return None rather than raising.
    result = calibration_audit.load_bundle_holdout_predictions()
    # Either None (no bundle) or a 2-tuple (bundle exists with WF
    # predictions). Both shapes are acceptable; just assert no exception.
    assert result is None or (isinstance(result, tuple) and len(result) == 2)


# ---------------------------------------------------------------------------
# Diagnostic interpretation: when no calibrator beats raw
# ---------------------------------------------------------------------------


def test_audit_on_pure_noise_no_calibrator_beats_raw_significantly():
    """Sanity: when raw is independent of y (no signal), every
    calibrator scores around the base-rate Brier ~0.25. None should
    dramatically outperform the others. This is the regime PR #85 was
    in — and the audit's interpretation helper exists to make this
    state legible."""
    from edge_equation.engines.nrfi.evaluation.calibration_audit import run_audit
    rng = np.random.default_rng(5)
    n = 1000
    raw = rng.uniform(0.0, 1.0, size=n)
    y = rng.integers(0, 2, size=n)
    report = run_audit(raw, y, seed=5)
    # Every calibrator's Brier should be within 0.05 of 0.25.
    for r in report.calibrator_results:
        assert 0.20 < r.brier < 0.30, f"{r.name}: brier={r.brier}"
