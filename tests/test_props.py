from decimal import Decimal
import pytest

from edge_equation.math.props import (
    DEFAULT_ALPHA,
    NegativeBinomial,
    NegBinomParams,
    OverUnderProbs,
)


def test_neg_binom_params_variance_formula():
    # Var(X) = mu + alpha * mu^2
    p = NegBinomParams(mu=Decimal('5'), alpha=Decimal('0.2'))
    expected = Decimal('5') + Decimal('0.2') * Decimal('25')
    assert p.variance() == expected.quantize(Decimal('0.000001'))


def test_neg_binom_params_r_and_p_are_consistent():
    # r = 1/alpha; p = 1 / (1 + alpha*mu)
    p = NegBinomParams(mu=Decimal('4'), alpha=Decimal('0.25'))
    assert p.r() == Decimal('4').quantize(Decimal('0.000001'))
    assert p.p() == (Decimal('1') / Decimal('2')).quantize(Decimal('0.000001'))


def test_neg_binom_zero_alpha_rejected():
    p = NegBinomParams(mu=Decimal('5'), alpha=Decimal('0'))
    with pytest.raises(ValueError, match="alpha"):
        p.r()


def test_pmf_sums_to_approximately_one():
    # Summing PMF over a wide range should give ~1.0.
    params = NegBinomParams(mu=Decimal('3'), alpha=Decimal('0.25'))
    total = Decimal('0')
    for k in range(0, 60):
        total += NegativeBinomial.pmf(k, params)
    assert abs(total - Decimal('1')) < Decimal('0.01')


def test_pmf_negative_k_is_zero():
    params = NegBinomParams(mu=Decimal('3'), alpha=Decimal('0.25'))
    assert NegativeBinomial.pmf(-1, params) == Decimal('0').quantize(Decimal('0.000001'))


def test_cdf_monotone_non_decreasing():
    params = NegBinomParams(mu=Decimal('5'), alpha=Decimal('0.2'))
    prev = Decimal('0')
    for k in range(0, 30):
        cur = NegativeBinomial.cdf(k, params)
        assert cur >= prev
        prev = cur


def test_cdf_approaches_one_at_high_k():
    params = NegBinomParams(mu=Decimal('2'), alpha=Decimal('0.3'))
    assert NegativeBinomial.cdf(100, params) > Decimal('0.999')


def test_over_under_half_integer_zero_push():
    params = NegBinomParams(mu=Decimal('6'), alpha=Decimal('0.25'))
    probs = NegativeBinomial.over_under(Decimal('6.5'), params)
    assert isinstance(probs, OverUnderProbs)
    assert probs.p_push == Decimal('0').quantize(Decimal('0.000001'))
    # Probabilities sum to 1 within rounding
    assert abs(probs.p_over + probs.p_under + probs.p_push - Decimal('1')) < Decimal('0.00001')


def test_over_under_integer_line_has_push():
    params = NegBinomParams(mu=Decimal('6'), alpha=Decimal('0.25'))
    probs = NegativeBinomial.over_under(Decimal('6'), params)
    assert probs.p_push > Decimal('0')
    assert abs(probs.p_over + probs.p_under + probs.p_push - Decimal('1')) < Decimal('0.00001')


def test_over_under_higher_mean_more_over_probability():
    params_low = NegBinomParams(mu=Decimal('3'), alpha=Decimal('0.25'))
    params_high = NegBinomParams(mu=Decimal('10'), alpha=Decimal('0.25'))
    low = NegativeBinomial.over_under(Decimal('6.5'), params_low)
    high = NegativeBinomial.over_under(Decimal('6.5'), params_high)
    assert high.p_over > low.p_over


def test_expected_rate_returns_mu():
    params = NegBinomParams(mu=Decimal('7.25'), alpha=Decimal('0.2'))
    assert NegativeBinomial.expected_rate(params) == Decimal('7.250000')


def test_default_alpha_constant_reasonable():
    assert DEFAULT_ALPHA > Decimal('0')
    assert DEFAULT_ALPHA < Decimal('1')


def test_neg_binom_params_frozen():
    p = NegBinomParams(mu=Decimal('3'), alpha=Decimal('0.2'))
    with pytest.raises(Exception):
        p.mu = Decimal('99')


def test_to_dict_shape():
    p = NegBinomParams(mu=Decimal('3'), alpha=Decimal('0.2'))
    d = p.to_dict()
    assert d["mu"] == "3"
    assert "r" in d and "p" in d and "variance" in d


def test_over_under_to_dict_has_stringified_decimals():
    params = NegBinomParams(mu=Decimal('5'), alpha=Decimal('0.2'))
    probs = NegativeBinomial.over_under(Decimal('4.5'), params)
    d = probs.to_dict()
    assert isinstance(d["p_over"], str)
    assert isinstance(d["p_under"], str)
    assert d["line"] == "4.5"


def test_pmf_matches_poisson_as_alpha_approaches_zero():
    # In the limit alpha -> 0, negative binomial collapses to Poisson.
    # Use a very small alpha and compare PMF(k=mu) to the Poisson value.
    import math
    mu = 5
    params = NegBinomParams(mu=Decimal(mu), alpha=Decimal('0.001'))
    nb_pmf = float(NegativeBinomial.pmf(mu, params))
    pois_pmf = math.exp(-mu) * (mu ** mu) / math.factorial(mu)
    assert abs(nb_pmf - pois_pmf) < 0.01
