import pytest
from decimal import Decimal

from edge_equation.math.rho import (
    RhoParams,
    DixonColesRho,
    RHO_INIT_REGISTRY,
    RHO_LOWER_BOUND,
    RHO_UPPER_BOUND,
)


def test_registry_values():
    assert RHO_INIT_REGISTRY["SOCCER_TOP"] == Decimal('-0.13')
    assert RHO_INIT_REGISTRY["SOCCER_TIER2"] == Decimal('-0.08')
    assert RHO_INIT_REGISTRY["DEFAULT"] == Decimal('0.00')


def test_bounds():
    assert RHO_LOWER_BOUND == Decimal('-0.25')
    assert RHO_UPPER_BOUND == Decimal('0.15')


def test_for_tier_returns_params():
    p = DixonColesRho.for_tier("SOCCER_TOP")
    assert isinstance(p, RhoParams)
    assert p.tier == "SOCCER_TOP"
    assert p.rho == Decimal('-0.13')


def test_for_tier_unknown_raises():
    with pytest.raises(ValueError, match="Unknown tier"):
        DixonColesRho.for_tier("NONEXISTENT")


def test_clamp_rho_below_bound():
    assert DixonColesRho.clamp_rho(Decimal('-0.50')) == RHO_LOWER_BOUND


def test_clamp_rho_above_bound():
    assert DixonColesRho.clamp_rho(Decimal('0.99')) == RHO_UPPER_BOUND


def test_clamp_rho_inside_bounds():
    assert DixonColesRho.clamp_rho(Decimal('-0.13')) == Decimal('-0.13')
    assert DixonColesRho.clamp_rho(Decimal('0.05')) == Decimal('0.05')


def test_clamp_rho_at_bounds():
    assert DixonColesRho.clamp_rho(RHO_LOWER_BOUND) == RHO_LOWER_BOUND
    assert DixonColesRho.clamp_rho(RHO_UPPER_BOUND) == RHO_UPPER_BOUND


def test_tau_nonadjusted_cells_return_one():
    rho = Decimal('-0.13')
    assert DixonColesRho.tau(2, 0, 1.5, 1.1, rho) == Decimal('1.000000')
    assert DixonColesRho.tau(0, 2, 1.5, 1.1, rho) == Decimal('1.000000')
    assert DixonColesRho.tau(3, 3, 1.5, 1.1, rho) == Decimal('1.000000')
    assert DixonColesRho.tau(2, 1, 1.5, 1.1, rho) == Decimal('1.000000')
    assert DixonColesRho.tau(1, 2, 1.5, 1.1, rho) == Decimal('1.000000')


def test_tau_zero_zero_negative_rho_inflates():
    # tau(0,0) = 1 - lh*la*rho; negative rho -> tau > 1
    lh, la = 1.5, 1.1
    rho = Decimal('-0.13')
    expected = Decimal('1') - Decimal(str(lh)) * Decimal(str(la)) * rho
    assert DixonColesRho.tau(0, 0, lh, la, rho) == expected.quantize(Decimal('0.000001'))


def test_tau_one_one_negative_rho_inflates():
    # tau(1,1) = 1 - rho; negative rho -> tau > 1
    rho = Decimal('-0.13')
    expected = Decimal('1') - rho
    assert DixonColesRho.tau(1, 1, 1.5, 1.1, rho) == expected.quantize(Decimal('0.000001'))


def test_tau_zero_one_negative_rho_deflates():
    # tau(0,1) = 1 + lh*rho; negative rho -> tau < 1
    lh, la = 1.5, 1.1
    rho = Decimal('-0.13')
    expected = Decimal('1') + Decimal(str(lh)) * rho
    assert DixonColesRho.tau(0, 1, lh, la, rho) == expected.quantize(Decimal('0.000001'))


def test_tau_one_zero_negative_rho_deflates():
    # tau(1,0) = 1 + la*rho; negative rho -> tau < 1
    lh, la = 1.5, 1.1
    rho = Decimal('-0.13')
    expected = Decimal('1') + Decimal(str(la)) * rho
    assert DixonColesRho.tau(1, 0, lh, la, rho) == expected.quantize(Decimal('0.000001'))


def test_tau_rho_zero_all_cells_one():
    rho = Decimal('0.00')
    for x in range(3):
        for y in range(3):
            assert DixonColesRho.tau(x, y, 1.3, 1.2, rho) == Decimal('1.000000')


def test_rho_params_frozen():
    p = DixonColesRho.for_tier("DEFAULT")
    with pytest.raises(Exception):
        p.rho = Decimal('0.9')


def test_to_dict_has_string_values():
    p = DixonColesRho.for_tier("SOCCER_TOP")
    d = p.to_dict()
    assert d["tier"] == "SOCCER_TOP"
    assert d["rho"] == "-0.13"
