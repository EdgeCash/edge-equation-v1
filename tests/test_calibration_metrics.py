from decimal import Decimal
import pytest

from edge_equation.backtest.calibration import Calibration
from edge_equation.math.calibration_metrics import (
    CalibrationAnalytics,
    CalibrationMetrics,
)


def test_ece_zero_on_perfect_calibration():
    preds = [0.0, 1.0, 0.0, 1.0]
    outcomes = [0, 1, 0, 1]
    result = Calibration.compute(preds, outcomes, n_bins=10)
    assert CalibrationAnalytics.ece(result) == Decimal('0').quantize(Decimal('0.000001'))


def test_mce_zero_when_bin_means_match_exactly():
    # 10 preds at 0.5; 5 hit, 5 miss -> mean_outcome = 0.5 matches
    # mean_pred exactly. Only one occupied bin, so MCE = 0.
    preds = [0.5] * 10
    outcomes = [1] * 5 + [0] * 5
    result = Calibration.compute(preds, outcomes, n_bins=10)
    assert CalibrationAnalytics.mce(result) == Decimal('0').quantize(Decimal('0.000001'))


def test_ece_nonzero_when_miscalibrated():
    # All predictions 0.5 but outcomes are always 1 -> huge miscalibration.
    preds = [0.5] * 10
    outcomes = [1] * 10
    result = Calibration.compute(preds, outcomes, n_bins=5)
    ece = CalibrationAnalytics.ece(result)
    # gap is ~0.5; ECE should be close to that.
    assert ece > Decimal('0.4')


def test_mce_dominates_when_one_bin_is_very_wrong():
    # Mostly calibrated with one catastrophically wrong bin.
    preds = [0.05] * 10 + [0.95] * 10 + [0.5]
    outcomes = [0] * 10 + [1] * 10 + [0]
    result = Calibration.compute(preds, outcomes, n_bins=10)
    # The middle bin should be ~0.5 gap -> MCE >= 0.4.
    assert CalibrationAnalytics.mce(result) > Decimal('0.4')


def test_from_result_reliability_frac_bounded():
    preds = [0.1, 0.3, 0.5, 0.7, 0.9]
    outcomes = [0, 0, 1, 1, 1]
    metrics = CalibrationAnalytics.compute(preds, outcomes, n_bins=5)
    assert isinstance(metrics, CalibrationMetrics)
    assert metrics.reliability_frac >= Decimal('0')
    assert metrics.resolution_frac >= Decimal('0')


def test_compute_matches_from_result():
    preds = [0.2, 0.4, 0.6, 0.8]
    outcomes = [0, 1, 1, 1]
    result = Calibration.compute(preds, outcomes, n_bins=5)
    via_result = CalibrationAnalytics.from_result(result)
    via_compute = CalibrationAnalytics.compute(preds, outcomes, n_bins=5)
    assert via_result.ece == via_compute.ece
    assert via_result.mce == via_compute.mce
    assert via_result.brier == via_compute.brier


def test_metrics_to_dict_shape():
    preds = [0.5] * 4
    outcomes = [0, 1, 0, 1]
    metrics = CalibrationAnalytics.compute(preds, outcomes, n_bins=5)
    d = metrics.to_dict()
    for key in ("ece", "mce", "brier", "log_loss", "reliability_frac", "resolution_frac", "n"):
        assert key in d


def test_metrics_frozen():
    preds = [0.5]
    outcomes = [1]
    metrics = CalibrationAnalytics.compute(preds, outcomes, n_bins=5)
    with pytest.raises(Exception):
        metrics.ece = Decimal('0.99')


def test_zero_brier_produces_zero_fracs():
    # Perfect preds -> brier ~= 0 -> reliability_frac / resolution_frac = 0
    preds = [0.0, 1.0] * 5
    outcomes = [0, 1] * 5
    metrics = CalibrationAnalytics.compute(preds, outcomes, n_bins=10)
    assert metrics.reliability_frac == Decimal('0').quantize(Decimal('0.000001'))
    assert metrics.resolution_frac == Decimal('0').quantize(Decimal('0.000001'))


def test_ece_empty_bins_ignored():
    # Only one occupied bin -> ECE just reports that bin's gap.
    preds = [0.95] * 5
    outcomes = [1, 1, 0, 1, 1]
    result = Calibration.compute(preds, outcomes, n_bins=10)
    ece = CalibrationAnalytics.ece(result)
    # mean_pred ~= 0.95; mean_outcome = 0.8; gap ~= 0.15
    assert Decimal('0.10') < ece < Decimal('0.20')
