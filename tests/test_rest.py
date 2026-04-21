import pytest
from decimal import Decimal

from edge_equation.context.rest import (
    RestContext,
    RestAdjuster,
    REST_COEFFICIENTS,
)
from edge_equation.context.adjustment import ContextAdjustment


def test_equal_rest_yields_zero_home_adv():
    ctx = RestContext(sport="NBA", home_rest_days=2, away_rest_days=2)
    a = RestAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_more_home_rest_positive_delta():
    ctx = RestContext(sport="NBA", home_rest_days=3, away_rest_days=1)
    a = RestAdjuster.adjustment(ctx)
    assert a.home_adv_delta == (REST_COEFFICIENTS["NBA"] * Decimal('2')).quantize(Decimal('0.000001'))


def test_more_away_rest_negative_delta():
    ctx = RestContext(sport="NFL", home_rest_days=6, away_rest_days=8)
    a = RestAdjuster.adjustment(ctx)
    assert a.home_adv_delta == (REST_COEFFICIENTS["NFL"] * Decimal('-2')).quantize(Decimal('0.000001'))


def test_unknown_sport_zero_coefficient():
    ctx = RestContext(sport="CRICKET", home_rest_days=4, away_rest_days=1)
    a = RestAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_components_include_diff_and_coefficient():
    ctx = RestContext(sport="NBA", home_rest_days=3, away_rest_days=1)
    a = RestAdjuster.adjustment(ctx)
    assert a.components["source"] == "rest"
    assert a.components["diff_days"] == 2
    assert a.components["coefficient"] == str(REST_COEFFICIENTS["NBA"])


def test_returns_adjustment_type():
    ctx = RestContext(sport="MLB", home_rest_days=1, away_rest_days=1)
    a = RestAdjuster.adjustment(ctx)
    assert isinstance(a, ContextAdjustment)


def test_rest_context_frozen():
    ctx = RestContext(sport="NBA", home_rest_days=2, away_rest_days=2)
    with pytest.raises(Exception):
        ctx.home_rest_days = 999


def test_totals_delta_always_zero():
    ctx = RestContext(sport="NHL", home_rest_days=3, away_rest_days=0)
    a = RestAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))
