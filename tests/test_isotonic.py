from decimal import Decimal
import pytest

from edge_equation.math.isotonic import (
    IsotonicBlock,
    IsotonicFit,
    IsotonicRegressor,
)


def test_fit_empty():
    fit = IsotonicRegressor.fit([], [])
    assert fit.blocks == tuple()
    assert IsotonicRegressor.predict(fit, 0.5) == Decimal('0').quantize(Decimal('0.000001'))


def test_fit_already_monotone_no_merges():
    fit = IsotonicRegressor.fit([1, 2, 3, 4], [0.1, 0.3, 0.5, 0.7])
    assert len(fit.blocks) == 4
    # Each point stays its own block
    for b in fit.blocks:
        assert b.total_weight == Decimal('1')


def test_fit_violators_merge():
    # Inputs violate monotonicity at (2, 0.8) -> (3, 0.4). PAV merges those
    # two into a pool with mean 0.6. The trailing (4, 0.6) ties with, but
    # does not violate, the pooled mean -- so it stays as a separate block
    # under the strict-less-than rule.
    fit = IsotonicRegressor.fit([1, 2, 3, 4], [0.2, 0.8, 0.4, 0.6])
    assert len(fit.blocks) == 3
    assert fit.blocks[0].mean_y == Decimal('0.2').quantize(Decimal('0.000001'))
    assert abs(fit.blocks[1].mean_y - Decimal('0.6')) < Decimal('0.000002')
    assert abs(fit.blocks[2].mean_y - Decimal('0.6')) < Decimal('0.000002')


def test_fit_all_decreasing_collapses_to_one_block():
    fit = IsotonicRegressor.fit([1, 2, 3, 4], [0.9, 0.7, 0.5, 0.3])
    # Full monotonicity violation -> one block with mean = 0.6
    assert len(fit.blocks) == 1
    assert abs(fit.blocks[0].mean_y - Decimal('0.6')) < Decimal('0.000002')


def test_predict_clamps_below_first_block():
    fit = IsotonicRegressor.fit([2, 4, 6], [0.1, 0.5, 0.9])
    assert IsotonicRegressor.predict(fit, 0) == Decimal('0.1').quantize(Decimal('0.000001'))


def test_predict_clamps_above_last_block():
    fit = IsotonicRegressor.fit([2, 4, 6], [0.1, 0.5, 0.9])
    assert IsotonicRegressor.predict(fit, 10) == Decimal('0.9').quantize(Decimal('0.000001'))


def test_predict_within_block_returns_block_mean():
    fit = IsotonicRegressor.fit([1, 1, 1], [0.2, 0.4, 0.6])
    # Three points tied at x=1 merge into one block with mean 0.4
    assert len(fit.blocks) == 1
    assert abs(IsotonicRegressor.predict(fit, 1) - Decimal('0.4')) < Decimal('0.000002')


def test_predict_interpolates_between_blocks():
    # Two isolated blocks at x=2 (mean 0.1) and x=6 (mean 0.9). A query at
    # x=4 should fall in the gap -> halfway between them = 0.5.
    fit = IsotonicRegressor.fit([2, 6], [0.1, 0.9])
    assert abs(IsotonicRegressor.predict(fit, 4) - Decimal('0.5')) < Decimal('0.000002')


def test_predict_batch_matches_per_point():
    fit = IsotonicRegressor.fit([1, 2, 3, 4], [0.1, 0.4, 0.6, 0.9])
    xs = [1.5, 2.5, 3.5]
    per_point = [IsotonicRegressor.predict(fit, x) for x in xs]
    batch = IsotonicRegressor.predict_batch(fit, xs)
    assert per_point == batch


def test_weighted_merge_weights_correctly():
    # With weights 10 on y=0.8 and 1 on y=0.0, the merge mean is (10*0.8+1*0) / 11
    fit = IsotonicRegressor.fit(
        xs=[1, 2],
        ys=[0.8, 0.0],  # violates monotonicity -> merge
        weights=[10, 1],
    )
    assert len(fit.blocks) == 1
    expected = Decimal('10') * Decimal('0.8') / Decimal('11')
    assert abs(fit.blocks[0].mean_y - expected.quantize(Decimal('0.000001'))) < Decimal('0.000002')


def test_zero_or_negative_weights_rejected():
    with pytest.raises(ValueError, match="weights"):
        IsotonicRegressor.fit([1, 2], [0.1, 0.2], weights=[1, 0])


def test_length_mismatch_rejected():
    with pytest.raises(ValueError, match="length"):
        IsotonicRegressor.fit([1, 2], [0.1])
    with pytest.raises(ValueError, match="length"):
        IsotonicRegressor.fit([1, 2], [0.1, 0.2], weights=[1])


def test_decreasing_mode_flips_monotonicity():
    fit = IsotonicRegressor.fit([1, 2, 3, 4], [0.9, 0.6, 0.4, 0.1], increasing=False)
    # Already monotone-decreasing; every block separate.
    assert len(fit.blocks) == 4
    # Predict should respect the decreasing shape
    assert IsotonicRegressor.predict(fit, 1) > IsotonicRegressor.predict(fit, 4)


def test_determinism_same_inputs_same_fit():
    xs = [1, 2, 3, 4, 5, 6]
    ys = [0.1, 0.3, 0.2, 0.6, 0.5, 0.8]
    fit1 = IsotonicRegressor.fit(xs, ys)
    fit2 = IsotonicRegressor.fit(xs, ys)
    assert fit1.blocks == fit2.blocks


def test_isotonic_block_frozen():
    b = IsotonicBlock(
        left_x=Decimal('0'), right_x=Decimal('1'),
        mean_y=Decimal('0.5'), total_weight=Decimal('1'),
    )
    with pytest.raises(Exception):
        b.mean_y = Decimal('0.99')


def test_to_dict_shapes():
    fit = IsotonicRegressor.fit([1, 2], [0.3, 0.7])
    d = fit.to_dict()
    assert d["increasing"] is True
    assert len(d["blocks"]) == 2
    for b in d["blocks"]:
        assert "mean_y" in b and "total_weight" in b
