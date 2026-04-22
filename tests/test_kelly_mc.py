from decimal import Decimal
import pytest

from edge_equation.math.kelly_adaptive import AdaptiveKelly, KellyResult


def test_from_mc_uses_stdev_as_stderr():
    mc = {"p10": 0.45, "p50": 0.55, "p90": 0.65, "mean": 0.55, "stdev": 0.08}
    result = AdaptiveKelly.from_mc(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        mc_result=mc,
        sample_size=100,
        portfolio_size=1,
    )
    assert isinstance(result, KellyResult)
    # Non-zero uncertainty factor less than 1.0 (finite stdev shrinks Kelly)
    assert Decimal('0') < result.uncertainty_factor < Decimal('1')


def test_from_mc_zero_stdev_yields_one_uncertainty_factor():
    mc = {"p10": 0.55, "p50": 0.55, "p90": 0.55, "mean": 0.55, "stdev": 0.0}
    result = AdaptiveKelly.from_mc(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        mc_result=mc,
        sample_size=100,
        portfolio_size=1,
    )
    assert result.uncertainty_factor == Decimal('1').quantize(Decimal('0.000001'))


def test_from_mc_falls_back_to_quantile_spread_when_stdev_missing():
    # (p90 - p10) / 2.56 approximates a normal stdev.
    mc = {"p10": 0.40, "p50": 0.55, "p90": 0.70, "mean": 0.55}  # no stdev
    result = AdaptiveKelly.from_mc(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        mc_result=mc,
        sample_size=100,
        portfolio_size=1,
    )
    assert result.uncertainty_factor < Decimal('1')


def test_from_mc_high_variance_shrinks_kelly_more():
    low_var = AdaptiveKelly.from_mc(
        edge=Decimal('0.05'), decimal_odds=Decimal('2.0'),
        mc_result={"p10": 0.53, "p50": 0.55, "p90": 0.57, "mean": 0.55, "stdev": 0.02},
        sample_size=1000, portfolio_size=1,
    )
    high_var = AdaptiveKelly.from_mc(
        edge=Decimal('0.05'), decimal_odds=Decimal('2.0'),
        mc_result={"p10": 0.35, "p50": 0.55, "p90": 0.75, "mean": 0.55, "stdev": 0.20},
        sample_size=1000, portfolio_size=1,
    )
    assert low_var.kelly_final > high_var.kelly_final


def test_from_mc_no_stdev_no_quantiles_treats_as_zero_variance():
    # Missing p10/p90 AND stdev -> stdev assumed 0 -> uncertainty factor 1.0.
    mc = {"mean": 0.55}
    result = AdaptiveKelly.from_mc(
        edge=Decimal('0.05'),
        decimal_odds=Decimal('2.0'),
        mc_result=mc,
        sample_size=100,
        portfolio_size=1,
    )
    assert result.uncertainty_factor == Decimal('1').quantize(Decimal('0.000001'))
