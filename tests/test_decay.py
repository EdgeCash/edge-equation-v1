import math
import pytest
from decimal import Decimal

from edge_equation.math.decay import (
    DecayParams,
    DecayWeights,
    DECAY_XI_REGISTRY,
)


def test_registry_values():
    assert DECAY_XI_REGISTRY["SOCCER"] == Decimal('0.0020')
    assert DECAY_XI_REGISTRY["NFL"] == Decimal('0.0040')
    assert DECAY_XI_REGISTRY["NBA"] == Decimal('0.0055')
    assert DECAY_XI_REGISTRY["NHL"] == Decimal('0.0040')
    assert DECAY_XI_REGISTRY["MLB"] == Decimal('0.0025')


def test_for_sport_returns_params():
    p = DecayWeights.for_sport("MLB")
    assert isinstance(p, DecayParams)
    assert p.sport == "MLB"
    assert p.xi == Decimal('0.0025')


def test_for_sport_unknown_raises():
    with pytest.raises(ValueError, match="Unknown sport"):
        DecayWeights.for_sport("CRICKET")


def test_halflife_matches_ln2_over_xi():
    p = DecayWeights.for_sport("MLB")
    expected = Decimal(str(math.log(2))) / p.xi
    assert p.halflife_days() == expected.quantize(Decimal('0.000001'))


def test_weight_at_zero_age_is_one():
    xi = Decimal('0.0025')
    w = DecayWeights.weight(0.0, xi)
    assert w == Decimal('1.000000')


def test_weight_decreases_with_age():
    xi = Decimal('0.0025')
    w0 = DecayWeights.weight(0.0, xi)
    w30 = DecayWeights.weight(30.0, xi)
    w365 = DecayWeights.weight(365.0, xi)
    assert w0 > w30 > w365


def test_weight_at_halflife_is_half():
    p = DecayWeights.for_sport("MLB")
    hl = float(p.halflife_days())
    w = DecayWeights.weight(hl, p.xi)
    assert abs(w - Decimal('0.5')) < Decimal('0.00001')


def test_weight_negative_age_raises():
    with pytest.raises(ValueError, match=">= 0"):
        DecayWeights.weight(-1.0, Decimal('0.0025'))


def test_apply_returns_list_of_weights():
    xi = Decimal('0.0040')
    weights = DecayWeights.apply([0.0, 100.0, 200.0], xi)
    assert len(weights) == 3
    assert weights[0] > weights[1] > weights[2]


def test_weighted_mean_equal_ages_collapses_to_simple_mean():
    xi = Decimal('0.0025')
    # All ages 0 => all weights 1 => simple mean
    result = DecayWeights.weighted_mean([10.0, 20.0, 30.0], [0.0, 0.0, 0.0], xi)
    assert result == Decimal('20.000000')


def test_weighted_mean_recent_dominates():
    xi = Decimal('0.0055')  # NBA-like: fast forgetting
    # Old observation at 0, recent at 365 days ago reversed:
    # Weight at 0 days >> weight at 365 days, so result close to first value
    recent_val, old_val = 100.0, 0.0
    r = DecayWeights.weighted_mean([recent_val, old_val], [0.0, 365.0], xi)
    assert r > Decimal('80')


def test_weighted_mean_empty_returns_zero():
    r = DecayWeights.weighted_mean([], [], Decimal('0.0025'))
    assert r == Decimal('0').quantize(Decimal('0.000001'))


def test_weighted_mean_length_mismatch_raises():
    with pytest.raises(ValueError, match="equal length"):
        DecayWeights.weighted_mean([1.0, 2.0], [0.0], Decimal('0.0025'))


def test_decay_params_frozen():
    p = DecayWeights.for_sport("NFL")
    with pytest.raises(Exception):
        p.xi = Decimal('0.1')


def test_to_dict_has_string_values():
    p = DecayWeights.for_sport("NBA")
    d = p.to_dict()
    assert d["sport"] == "NBA"
    assert d["xi"] == "0.0055"
    assert isinstance(d["halflife_days"], str)
