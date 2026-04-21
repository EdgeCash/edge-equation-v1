import pytest
from decimal import Decimal

from edge_equation.context.travel import (
    TravelContext,
    TravelAdjuster,
    MILES_COEFFICIENT,
    TZ_COEFFICIENT_PER_HOUR,
)


def test_zero_travel_zero_delta():
    ctx = TravelContext(sport="NBA", away_travel_miles=0.0, timezone_change_hours=0)
    a = TravelAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_miles_only_contribution():
    ctx = TravelContext(sport="NBA", away_travel_miles=1000.0, timezone_change_hours=0)
    a = TravelAdjuster.adjustment(ctx)
    expected = (Decimal('1000') * MILES_COEFFICIENT).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_tz_only_contribution():
    ctx = TravelContext(sport="NFL", away_travel_miles=0.0, timezone_change_hours=3)
    a = TravelAdjuster.adjustment(ctx)
    expected = (Decimal('3') * TZ_COEFFICIENT_PER_HOUR["NFL"]).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_miles_and_tz_sum():
    ctx = TravelContext(sport="MLB", away_travel_miles=2000.0, timezone_change_hours=2)
    a = TravelAdjuster.adjustment(ctx)
    miles_term = Decimal('2000') * MILES_COEFFICIENT
    tz_term = Decimal('2') * TZ_COEFFICIENT_PER_HOUR["MLB"]
    assert a.home_adv_delta == (miles_term + tz_term).quantize(Decimal('0.000001'))


def test_negative_miles_absolute_value():
    ctx = TravelContext(sport="NBA", away_travel_miles=-500.0, timezone_change_hours=0)
    a = TravelAdjuster.adjustment(ctx)
    expected = (Decimal('500') * MILES_COEFFICIENT).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_negative_tz_absolute_value():
    ctx = TravelContext(sport="NBA", away_travel_miles=0.0, timezone_change_hours=-3)
    a = TravelAdjuster.adjustment(ctx)
    expected = (Decimal('3') * TZ_COEFFICIENT_PER_HOUR["NBA"]).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_unknown_sport_only_miles_contribute():
    ctx = TravelContext(sport="CRICKET", away_travel_miles=1000.0, timezone_change_hours=5)
    a = TravelAdjuster.adjustment(ctx)
    expected = (Decimal('1000') * MILES_COEFFICIENT).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_totals_delta_always_zero():
    ctx = TravelContext(sport="NFL", away_travel_miles=3000.0, timezone_change_hours=3)
    a = TravelAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_components_have_miles_and_tz_terms():
    ctx = TravelContext(sport="MLB", away_travel_miles=1000.0, timezone_change_hours=1)
    a = TravelAdjuster.adjustment(ctx)
    assert a.components["source"] == "travel"
    assert "miles_term" in a.components
    assert "tz_term" in a.components


def test_travel_context_frozen():
    ctx = TravelContext(sport="NBA", away_travel_miles=500.0, timezone_change_hours=1)
    with pytest.raises(Exception):
        ctx.away_travel_miles = 9999.0
