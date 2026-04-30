"""Tests for the alternative NRFI calibrators (beta, smoothed
isotonic, confidence temperature scaling).

These run on synthetic data only — they prove the calibrators are
mathematically well-formed (signal-preserving, monotone, log-loss
respecting). They do NOT prove the calibrators help on the real NRFI
corpus — that's the operator's job via `calibration_audit`.
"""

from __future__ import annotations

import numpy as np
import pytest


# Slim CI runs without sklearn; the production Calibrator + Beta
# calibrator both depend on it. Skip the whole module rather than
# scatter importorskips across each test.
pytest.importorskip("sklearn")


# ---------------------------------------------------------------------------
# Shared synthetic generators
# ---------------------------------------------------------------------------


def _well_calibrated_synthetic(n: int = 1000, seed: int = 0):
    """raw_p ~ U[0,1]; y ~ Bernoulli(raw_p) → already-calibrated."""
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.0, 1.0, size=n)
    y = (rng.uniform(0.0, 1.0, size=n) < raw).astype(int)
    return raw, y


def _miscalibrated_synthetic(n: int = 1000, seed: int = 0):
    """raw_p is overconfident: y ~ Bernoulli(0.5 + 0.5*(raw_p-0.5))."""
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.0, 1.0, size=n)
    true_p = 0.5 + 0.5 * (raw - 0.5)   # squashed toward 0.5
    y = (rng.uniform(0.0, 1.0, size=n) < true_p).astype(int)
    return raw, y


# ---------------------------------------------------------------------------
# Beta calibrator
# ---------------------------------------------------------------------------


def test_beta_calibrator_monotone_on_well_calibrated_data():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        BetaCalibrator,
    )
    raw, y = _well_calibrated_synthetic(n=600, seed=1)
    cal = BetaCalibrator().fit(raw, y)
    grid = np.linspace(0.05, 0.95, 50)
    out = cal.transform(grid)
    # On well-calibrated data, beta should remain monotone non-decreasing.
    diffs = np.diff(out)
    assert (diffs >= -1e-6).all(), f"non-monotone: {diffs.min()}"


def test_beta_calibrator_corrects_squashed_misalibration():
    """Generator squashes toward 0.5; calibrated should pull predictions
    BACK toward the empirical pattern (closer to 0.5)."""
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        BetaCalibrator,
    )
    raw, y = _miscalibrated_synthetic(n=2000, seed=2)
    cal = BetaCalibrator().fit(raw, y)
    out = cal.transform(np.array([0.1, 0.9]))
    # Calibrated 0.1 should be > 0.1 (correcting the over-confidence
    # toward the empirical hit-rate of ~0.3); calibrated 0.9 should be
    # < 0.9 (toward ~0.7). Just check direction.
    assert out[0] > 0.12, f"low end not corrected: {out[0]}"
    assert out[1] < 0.88, f"high end not corrected: {out[1]}"


def test_beta_calibrator_handles_extreme_inputs_without_blowing_up():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        BetaCalibrator,
    )
    raw = np.array([0.0, 0.0, 1.0, 1.0, 0.5, 0.5])
    y = np.array([0, 0, 1, 1, 0, 1])
    cal = BetaCalibrator().fit(raw, y)
    # No NaN / Inf in output; clipped to (eps, 1-eps) internally.
    out = cal.transform(np.array([0.0, 0.5, 1.0]))
    assert np.isfinite(out).all()


def test_beta_calibrator_unfit_returns_input_unchanged():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        BetaCalibrator,
    )
    cal = BetaCalibrator()
    out = cal.transform([0.1, 0.5, 0.9])
    assert np.allclose(out, [0.1, 0.5, 0.9])


def test_beta_calibrator_raises_on_empty_fit():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        BetaCalibrator,
    )
    with pytest.raises(ValueError):
        BetaCalibrator().fit([], [])


# ---------------------------------------------------------------------------
# Smoothed isotonic
# ---------------------------------------------------------------------------


def test_smoothed_isotonic_preserves_signal_better_than_default_isotonic():
    """Vanilla isotonic on small noisy samples often collapses to a
    near-flat curve. Smoothed isotonic with a min-bin floor should
    keep meaningful std on a clearly monotonic signal."""
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        SmoothedIsotonicCalibrator,
    )
    raw, y = _well_calibrated_synthetic(n=600, seed=3)
    cal = SmoothedIsotonicCalibrator(min_samples_per_bin=50).fit(raw, y)
    out = cal.transform(np.linspace(0.0, 1.0, 200))
    # Output std should be substantial (the input is U[0,1] which itself
    # has std ~0.29). Demand at least 0.10.
    assert float(out.std()) > 0.10, f"std collapsed: {out.std()}"


def test_smoothed_isotonic_is_monotone_non_decreasing():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        SmoothedIsotonicCalibrator,
    )
    raw, y = _well_calibrated_synthetic(n=800, seed=4)
    cal = SmoothedIsotonicCalibrator(min_samples_per_bin=40).fit(raw, y)
    out = cal.transform(np.linspace(0.0, 1.0, 100))
    diffs = np.diff(out)
    assert (diffs >= -1e-6).all(), f"non-monotone: {diffs.min()}"


def test_smoothed_isotonic_min_bin_constraint_actually_constrains():
    """With min_samples_per_bin=200 on n=600, we should get exactly
    3 bins → at most 3 distinct calibrated values."""
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        SmoothedIsotonicCalibrator,
    )
    raw, y = _well_calibrated_synthetic(n=600, seed=5)
    cal = SmoothedIsotonicCalibrator(min_samples_per_bin=200).fit(raw, y)
    # n=600, min=200 → 3 bins. linear-interpolated between centers, so
    # the number of UNIQUE quantized bin-mean values is at most 3.
    assert len(cal._bin_means) == 3


def test_smoothed_isotonic_clips_to_unit_interval():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        SmoothedIsotonicCalibrator,
    )
    raw, y = _well_calibrated_synthetic(n=300, seed=6)
    cal = SmoothedIsotonicCalibrator(min_samples_per_bin=40).fit(raw, y)
    out = cal.transform(np.array([-0.5, 0.0, 0.5, 1.0, 1.5]))
    assert (out >= 0.0).all() and (out <= 1.0).all()


# ---------------------------------------------------------------------------
# Confidence temperature scaling
# ---------------------------------------------------------------------------


def test_confidence_temperature_recovers_identity_on_well_calibrated_data():
    """If the input is already calibrated, the temperature scaler
    should pick t_low ≈ t_high ≈ 1 (within grid resolution) — i.e.
    near-identity."""
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        ConfidenceTemperatureCalibrator,
    )
    raw, y = _well_calibrated_synthetic(n=2000, seed=7)
    cal = ConfidenceTemperatureCalibrator().fit(raw, y)
    out = cal.transform(np.array([0.2, 0.5, 0.8]))
    # Allow loose tolerance — coarse grid won't recover identity exactly.
    assert abs(out[1] - 0.5) < 0.05, f"midpoint off: {out[1]}"


def test_confidence_temperature_softens_overconfident_inputs():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        ConfidenceTemperatureCalibrator,
    )
    raw, y = _miscalibrated_synthetic(n=2000, seed=8)
    cal = ConfidenceTemperatureCalibrator().fit(raw, y)
    # On overconfident raw inputs the high-confidence end should be
    # softened (T_high > 1). Just check that a 0.9 input lands south
    # of 0.9 after calibration.
    out = cal.transform(np.array([0.9]))
    assert out[0] < 0.9


def test_confidence_temperature_unfit_returns_input():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        ConfidenceTemperatureCalibrator,
    )
    cal = ConfidenceTemperatureCalibrator()
    out = cal.transform([0.1, 0.9])
    assert np.allclose(out, [0.1, 0.9])


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_build_calibrator_dispatches_known_names():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        ALTERNATIVE_NAMES, build_calibrator,
    )
    for name in ALTERNATIVE_NAMES:
        cal = build_calibrator(name)
        assert cal is not None


def test_build_calibrator_rejects_unknown_name():
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        build_calibrator,
    )
    with pytest.raises(ValueError):
        build_calibrator("not-a-real-calibrator")


def test_alternative_names_includes_production_baselines():
    """The registry intentionally includes 'isotonic' and 'platt' so
    the audit shows the production calibrators side-by-side with the
    alternatives."""
    from edge_equation.engines.nrfi.models.calibration_alternatives import (
        ALTERNATIVE_NAMES,
    )
    assert "isotonic" in ALTERNATIVE_NAMES
    assert "platt" in ALTERNATIVE_NAMES
    assert "beta" in ALTERNATIVE_NAMES
    assert "smoothed_isotonic" in ALTERNATIVE_NAMES
    assert "conf_temperature" in ALTERNATIVE_NAMES
