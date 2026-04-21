import pytest
from decimal import Decimal

from edge_equation.backtest.calibration import (
    Calibration,
    CalibrationResult,
    ReliabilityBin,
)


def test_perfect_calibration_zero_brier():
    preds = [0.0, 1.0, 0.0, 1.0]
    outcomes = [0, 1, 0, 1]
    b = Calibration.brier(preds, outcomes)
    assert b == Decimal('0').quantize(Decimal('0.000001'))


def test_worst_calibration_brier_one():
    preds = [1.0, 0.0, 1.0, 0.0]
    outcomes = [0, 1, 0, 1]
    b = Calibration.brier(preds, outcomes)
    assert b == Decimal('1').quantize(Decimal('0.000001'))


def test_brier_matches_manual_mean_squared_error():
    preds = [0.3, 0.7, 0.5, 0.9]
    outcomes = [0, 1, 1, 1]
    expected = sum((Decimal(str(p)) - Decimal(o)) ** 2 for p, o in zip(preds, outcomes)) / Decimal('4')
    assert Calibration.brier(preds, outcomes) == expected.quantize(Decimal('0.000001'))


def test_log_loss_perfect_is_near_zero():
    preds = [0.0, 1.0, 0.0, 1.0]
    outcomes = [0, 1, 0, 1]
    # clamped away from 0/1 so not exactly zero but tiny
    ll = Calibration.log_loss(preds, outcomes)
    assert ll < Decimal('0.00002')


def test_log_loss_positive_for_miscalibrated():
    preds = [0.5, 0.5, 0.5, 0.5]
    outcomes = [0, 1, 0, 1]
    ll = Calibration.log_loss(preds, outcomes)
    assert ll > Decimal('0.6')
    assert ll < Decimal('0.7')  # close to ln(2) ≈ 0.693


def test_validate_length_mismatch_raises():
    with pytest.raises(ValueError, match="equal length"):
        Calibration.brier([0.1, 0.2], [1])


def test_validate_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        Calibration.brier([], [])


def test_validate_out_of_range_pred_raises():
    with pytest.raises(ValueError, match="must be in"):
        Calibration.brier([1.5], [1])


def test_validate_bad_outcome_raises():
    with pytest.raises(ValueError, match="outcomes must be"):
        Calibration.brier([0.5], [2])


def test_compute_returns_result_with_n_bins():
    preds = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    outcomes = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
    r = Calibration.compute(preds, outcomes, n_bins=10)
    assert isinstance(r, CalibrationResult)
    assert len(r.bins) == 10
    assert r.n == 10


def test_murphy_decomposition_identity_exact_when_preds_equal_bin_means():
    # The Murphy identity BS = Reliability - Resolution + Uncertainty is exact
    # only when each prediction equals the mean prediction of its bin (the
    # binned Brier). We construct such a case: one prediction per bin, placed
    # at the bin midpoint.
    preds = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    outcomes = [0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    r = Calibration.compute(preds, outcomes, n_bins=10)
    reconstructed = r.reliability - r.resolution + r.uncertainty
    assert abs(r.brier - reconstructed) < Decimal('0.00001')


def test_murphy_decomposition_approximate_for_raw_preds():
    # For raw (unbinned) predictions, the identity holds up to the within-bin
    # variance of predictions. This is small for fine binning and well-spread
    # predictions.
    preds = [0.1, 0.2, 0.3, 0.6, 0.7, 0.8, 0.4, 0.5, 0.9, 0.1]
    outcomes = [0, 0, 1, 1, 1, 1, 0, 0, 1, 0]
    r = Calibration.compute(preds, outcomes, n_bins=10)
    reconstructed = r.reliability - r.resolution + r.uncertainty
    # Within-bin variance <= 0.05 with 10 bins of width 0.1
    assert abs(r.brier - reconstructed) < Decimal('0.05')


def test_uncertainty_formula_matches_base_rate():
    preds = [0.5] * 10
    outcomes = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    r = Calibration.compute(preds, outcomes, n_bins=10)
    # base_rate = 0.5; uncertainty = 0.25
    assert r.uncertainty == Decimal('0.250000')


def test_bin_counts_sum_to_n():
    preds = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.05]
    outcomes = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
    r = Calibration.compute(preds, outcomes, n_bins=10)
    assert sum(b.count for b in r.bins) == 10


def test_prediction_at_1_goes_into_last_bin():
    preds = [1.0]
    outcomes = [1]
    r = Calibration.compute(preds, outcomes, n_bins=10)
    assert r.bins[9].count == 1


def test_invalid_n_bins_raises():
    with pytest.raises(ValueError, match="n_bins"):
        Calibration.compute([0.5], [1], n_bins=0)


def test_reliability_bin_frozen():
    b = ReliabilityBin(
        bin_start=Decimal('0'),
        bin_end=Decimal('0.1'),
        mean_pred=Decimal('0.05'),
        mean_outcome=Decimal('0'),
        count=1,
    )
    with pytest.raises(Exception):
        b.count = 999


def test_result_to_dict_has_bins_list():
    r = Calibration.compute([0.5, 0.5], [0, 1], n_bins=5)
    d = r.to_dict()
    assert isinstance(d["bins"], list)
    assert isinstance(d["brier"], str)
