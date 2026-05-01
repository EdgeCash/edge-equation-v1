"""Tests for the production NRFI Calibrator class — covering the
new beta method that became default on 2026-05-01.

The beta path mirrors `BetaCalibrator` from
`models/calibration_alternatives.py` (which has its own deeper test
coverage); these tests pin the production-class integration:

* method='beta' fits and transforms without raising
* save/load round-trips the beta model state through pickle
* default config method is 'beta' (production cutover)
* the existing isotonic + platt paths still work
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


pytest.importorskip("sklearn")


def _well_calibrated_synthetic(n: int = 600, seed: int = 1):
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.0, 1.0, size=n)
    y = (rng.uniform(0.0, 1.0, size=n) < raw).astype(int)
    return raw, y


# ---------------------------------------------------------------------------
# Beta method
# ---------------------------------------------------------------------------


def test_beta_calibrator_fits_and_transforms():
    from edge_equation.engines.nrfi.models.calibration import Calibrator
    raw, y = _well_calibrated_synthetic()
    cal = Calibrator(method="beta").fit(raw, y)
    out = cal.transform([0.1, 0.5, 0.9])
    assert out.shape == (3,)
    assert np.isfinite(out).all()
    assert (out >= 0.0).all() and (out <= 1.0).all()


def test_beta_method_persists_three_parameters():
    """The internal model is a small dict carrying a, b, c, eps —
    that's what makes save/load cheap."""
    from edge_equation.engines.nrfi.models.calibration import Calibrator
    raw, y = _well_calibrated_synthetic()
    cal = Calibrator(method="beta").fit(raw, y)
    assert isinstance(cal._model, dict)
    for key in ("a", "b", "c", "eps"):
        assert key in cal._model


def test_beta_method_round_trips_through_pickle(tmp_path: Path):
    from edge_equation.engines.nrfi.models.calibration import Calibrator
    raw, y = _well_calibrated_synthetic()
    cal = Calibrator(method="beta").fit(raw, y)
    grid = np.linspace(0.05, 0.95, 50)
    expected = cal.transform(grid)

    path = tmp_path / "beta.pkl"
    cal.save(path)
    loaded = Calibrator.load(path)

    assert loaded.method == "beta"
    actual = loaded.transform(grid)
    assert np.allclose(actual, expected, rtol=1e-9, atol=1e-12)


def test_beta_method_handles_extreme_inputs_without_blowing_up():
    from edge_equation.engines.nrfi.models.calibration import Calibrator
    cal = Calibrator(method="beta").fit(
        [0.0, 0.0, 1.0, 1.0, 0.5, 0.5], [0, 0, 1, 1, 0, 1],
    )
    out = cal.transform([0.0, 0.5, 1.0])
    assert np.isfinite(out).all()


def test_beta_method_corrects_squashed_misalibration():
    """Generator squashes raw toward 0.5 — calibrated output should
    pull predictions back toward the empirical 0.3/0.7 reliability."""
    rng = np.random.default_rng(2)
    n = 2000
    raw = rng.uniform(0.0, 1.0, size=n)
    true_p = 0.5 + 0.5 * (raw - 0.5)
    y = (rng.uniform(0.0, 1.0, size=n) < true_p).astype(int)

    from edge_equation.engines.nrfi.models.calibration import Calibrator
    cal = Calibrator(method="beta").fit(raw, y)
    out = cal.transform(np.array([0.1, 0.9]))
    assert out[0] > 0.12
    assert out[1] < 0.88


# ---------------------------------------------------------------------------
# Existing methods still work after the dispatch refactor
# ---------------------------------------------------------------------------


def test_isotonic_method_still_fits_and_transforms():
    from edge_equation.engines.nrfi.models.calibration import Calibrator
    raw, y = _well_calibrated_synthetic()
    cal = Calibrator(method="isotonic").fit(raw, y)
    out = cal.transform([0.1, 0.5, 0.9])
    assert (out >= 0.0).all() and (out <= 1.0).all()


def test_platt_method_still_fits_and_transforms():
    from edge_equation.engines.nrfi.models.calibration import Calibrator
    raw, y = _well_calibrated_synthetic()
    cal = Calibrator(method="platt").fit(raw, y)
    out = cal.transform([0.1, 0.5, 0.9])
    assert (out >= 0.0).all() and (out <= 1.0).all()


def test_unknown_method_raises():
    from edge_equation.engines.nrfi.models.calibration import Calibrator
    with pytest.raises(ValueError):
        Calibrator(method="nonsense").fit([0.5], [0])


# ---------------------------------------------------------------------------
# Config default flipped to beta
# ---------------------------------------------------------------------------


def test_default_config_calibration_method_is_beta():
    """Beta became the production default on 2026-05-01 after the
    calibration audit. Pin it so a future drive-by edit doesn't
    silently revert to isotonic."""
    from edge_equation.engines.nrfi.config import get_default_config
    cfg = get_default_config()
    assert cfg.model.calibration_method == "beta"
