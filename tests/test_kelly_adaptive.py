import pytest
from decimal import Decimal

from edge_equation.math.kelly_adaptive import (
    AdaptiveKelly,
    KellyInputs,
    KellyResult,
    BASE_FRACTION,
    EDGE_FLOOR,
    PER_BET_CAP,
    DAILY_CAP,
    N_PRIOR,
    PORT_ALPHA,
)


def test_constants():
    assert BASE_FRACTION == Decimal('0.25')
    assert EDGE_FLOOR == Decimal('0.02')
    assert PER_BET_CAP == Decimal('0.05')
    assert DAILY_CAP == Decimal('0.25')
    assert N_PRIOR == 30
    assert PORT_ALPHA == Decimal('0.5')


def test_edge_below_floor_returns_zero():
    inp = KellyInputs(
        edge=Decimal('0.015'),
        decimal_odds=Decimal('2.0'),
        fair_prob_stderr=Decimal('0.01'),
        sample_size=100,
        portfolio_size=1,
        max_sibling_corr=Decimal('0'),
    )
    r = AdaptiveKelly.compute(inp)
    assert r.kelly_final == Decimal('0').quantize(Decimal('0.000001'))
    assert r.capped is False


def test_edge_at_floor_triggers_full_compute():
    # At exactly EDGE_FLOOR we go through the shrinkage stack.
    inp = KellyInputs(
        edge=Decimal('0.02'),
        decimal_odds=Decimal('2.0'),
        fair_prob_stderr=Decimal('0'),
        sample_size=1000,
        portfolio_size=1,
        max_sibling_corr=Decimal('0'),
    )
    r = AdaptiveKelly.compute(inp)
    assert r.full_kelly > Decimal('0')
    assert r.kelly_final > Decimal('0')


def test_sample_factor_zero_n_zeros_allocation():
    inp = KellyInputs(
        edge=Decimal('0.08'),
        decimal_odds=Decimal('2.0'),
        sample_size=0,
        portfolio_size=1,
    )
    r = AdaptiveKelly.compute(inp)
    assert r.sample_factor == Decimal('0').quantize(Decimal('0.000001'))
    assert r.kelly_final == Decimal('0').quantize(Decimal('0.000001'))


def test_sample_factor_formula():
    # n / (n + N_PRIOR); n=30 -> 0.5
    inp = KellyInputs(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        sample_size=30,
        portfolio_size=1,
    )
    r = AdaptiveKelly.compute(inp)
    assert r.sample_factor == Decimal('0.500000')


def test_portfolio_factor_formula():
    # 1 / (1 + alpha * (k - 1))
    for k, expected in [(1, Decimal('1')), (2, Decimal('1') / Decimal('1.5')), (3, Decimal('0.5'))]:
        inp = KellyInputs(
            edge=Decimal('0.05'),
            decimal_odds=Decimal('2.0'),
            sample_size=1000,
            portfolio_size=k,
        )
        r = AdaptiveKelly.compute(inp)
        assert r.portfolio_factor == expected.quantize(Decimal('0.000001'))


def test_portfolio_factor_invalid_k():
    with pytest.raises(ValueError, match=">= 1"):
        AdaptiveKelly.compute(KellyInputs(
            edge=Decimal('0.05'),
            decimal_odds=Decimal('2.0'),
            sample_size=100,
            portfolio_size=0,
        ))


def test_correlation_factor_formula():
    inp = KellyInputs(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        sample_size=1000,
        portfolio_size=1,
        max_sibling_corr=Decimal('0.3'),
    )
    r = AdaptiveKelly.compute(inp)
    assert r.correlation_factor == Decimal('0.700000')


def test_correlation_factor_clamped_above_one():
    inp = KellyInputs(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        sample_size=1000,
        portfolio_size=1,
        max_sibling_corr=Decimal('1.5'),
    )
    r = AdaptiveKelly.compute(inp)
    assert r.correlation_factor == Decimal('0.000000')


def test_correlation_factor_clamped_below_zero():
    inp = KellyInputs(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        sample_size=1000,
        portfolio_size=1,
        max_sibling_corr=Decimal('-0.2'),
    )
    r = AdaptiveKelly.compute(inp)
    assert r.correlation_factor == Decimal('1.000000')


def test_uncertainty_factor_sigma_zero_returns_one():
    inp = KellyInputs(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        fair_prob_stderr=Decimal('0'),
        sample_size=1000,
        portfolio_size=1,
    )
    r = AdaptiveKelly.compute(inp)
    assert r.uncertainty_factor == Decimal('1.000000')


def test_uncertainty_factor_formula():
    # e^2 / (e^2 + sigma^2); edge=0.05, sigma=0.05 -> 0.5
    inp = KellyInputs(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        fair_prob_stderr=Decimal('0.05'),
        sample_size=1000,
        portfolio_size=1,
    )
    r = AdaptiveKelly.compute(inp)
    assert r.uncertainty_factor == Decimal('0.500000')


def test_full_kelly_formula():
    # edge / (dec_odds - 1); 0.05 / 1.0 = 0.05
    inp = KellyInputs(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        sample_size=1000,
        portfolio_size=1,
    )
    r = AdaptiveKelly.compute(inp)
    assert r.full_kelly == Decimal('0.050000')


def test_per_bet_cap_applied():
    # Large edge + favorable odds + all factors ~1 -> would exceed PER_BET_CAP
    inp = KellyInputs(
        edge=Decimal('0.40'),
        decimal_odds=Decimal('2.0'),
        fair_prob_stderr=Decimal('0'),
        sample_size=100000,
        portfolio_size=1,
        max_sibling_corr=Decimal('0'),
    )
    r = AdaptiveKelly.compute(inp)
    assert r.kelly_final == PER_BET_CAP.quantize(Decimal('0.000001'))
    assert r.capped is True


def test_multiplicative_stack_matches_manual():
    edge = Decimal('0.10')
    odds = Decimal('2.0')
    sigma = Decimal('0.05')
    n = 30
    k = 2
    corr = Decimal('0.2')
    inp = KellyInputs(
        edge=edge,
        decimal_odds=odds,
        fair_prob_stderr=sigma,
        sample_size=n,
        portfolio_size=k,
        max_sibling_corr=corr,
    )
    r = AdaptiveKelly.compute(inp)

    full = edge / (odds - Decimal('1'))
    unc = (edge * edge) / (edge * edge + sigma * sigma)
    samp = Decimal(n) / (Decimal(n) + Decimal(N_PRIOR))
    port = Decimal('1') / (Decimal('1') + PORT_ALPHA * (Decimal(k) - Decimal('1')))
    cor = Decimal('1') - corr
    expected_pre = BASE_FRACTION * full * unc * samp * port * cor
    assert r.pre_cap == expected_pre.quantize(Decimal('0.000001'))


def test_apply_daily_cap_under_budget():
    r = AdaptiveKelly.apply_daily_cap(Decimal('0.04'), Decimal('0.10'))
    assert r == Decimal('0.04').quantize(Decimal('0.000001'))


def test_apply_daily_cap_trims():
    # running=0.22, candidate=0.05, cap=0.25 -> trim to 0.03
    r = AdaptiveKelly.apply_daily_cap(Decimal('0.05'), Decimal('0.22'))
    assert r == Decimal('0.03').quantize(Decimal('0.000001'))


def test_apply_daily_cap_at_or_over_cap_returns_zero():
    assert AdaptiveKelly.apply_daily_cap(Decimal('0.05'), Decimal('0.25')) == Decimal('0').quantize(Decimal('0.000001'))
    assert AdaptiveKelly.apply_daily_cap(Decimal('0.05'), Decimal('0.30')) == Decimal('0').quantize(Decimal('0.000001'))


def test_kelly_inputs_frozen():
    inp = KellyInputs(edge=Decimal('0.05'), decimal_odds=Decimal('2.0'), sample_size=100)
    with pytest.raises(Exception):
        inp.edge = Decimal('0.99')


def test_kelly_result_frozen():
    inp = KellyInputs(edge=Decimal('0.05'), decimal_odds=Decimal('2.0'), sample_size=100, portfolio_size=1)
    r = AdaptiveKelly.compute(inp)
    with pytest.raises(Exception):
        r.kelly_final = Decimal('0.99')


def test_to_dict_has_string_values():
    inp = KellyInputs(edge=Decimal('0.05'), decimal_odds=Decimal('2.0'), sample_size=100, portfolio_size=1)
    r = AdaptiveKelly.compute(inp)
    d = r.to_dict()
    assert isinstance(d["kelly_final"], str)
    assert isinstance(d["full_kelly"], str)
    assert isinstance(d["capped"], bool)
