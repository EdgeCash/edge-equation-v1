import pytest
from decimal import Decimal

from edge_equation.context.weather import (
    WeatherContext,
    WeatherAdjuster,
    WIND_COEF,
    COLD_COEF,
    PRECIP_COEF,
    WIND_THRESHOLD_MPH,
    COLD_THRESHOLD_F,
)


def test_calm_warm_dry_zero_delta():
    ctx = WeatherContext(sport="MLB", temperature_f=70.0, wind_mph=5.0, precipitation_pct=0.0)
    a = WeatherAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_wind_below_threshold_no_effect():
    ctx = WeatherContext(sport="NFL", temperature_f=70.0, wind_mph=10.0, precipitation_pct=0.0)
    a = WeatherAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_wind_above_threshold_mlb():
    ctx = WeatherContext(sport="MLB", temperature_f=70.0, wind_mph=25.0, precipitation_pct=0.0)
    a = WeatherAdjuster.adjustment(ctx)
    # wind-10 = 15 mph over; 15 * -0.08
    expected = (WIND_COEF["MLB"] * Decimal('15')).quantize(Decimal('0.000001'))
    assert a.totals_delta == expected


def test_cold_below_threshold_nfl():
    ctx = WeatherContext(sport="NFL", temperature_f=20.0, wind_mph=0.0, precipitation_pct=0.0)
    a = WeatherAdjuster.adjustment(ctx)
    # 40-20 = 20, divided by 10 = 2.0; * -0.05
    expected = (COLD_COEF["NFL"] * Decimal('20') / Decimal('10')).quantize(Decimal('0.000001'))
    assert a.totals_delta == expected


def test_cold_at_threshold_no_cold_effect():
    ctx = WeatherContext(sport="NFL", temperature_f=40.0, wind_mph=0.0, precipitation_pct=0.0)
    a = WeatherAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_precipitation_nfl():
    ctx = WeatherContext(sport="NFL", temperature_f=70.0, wind_mph=0.0, precipitation_pct=50.0)
    a = WeatherAdjuster.adjustment(ctx)
    # 50/100 * -0.04 = -0.02
    expected = (PRECIP_COEF["NFL"] * Decimal('0.5')).quantize(Decimal('0.000001'))
    assert a.totals_delta == expected


def test_indoor_sport_nba_zero_regardless_of_weather():
    ctx = WeatherContext(sport="NBA", temperature_f=-10.0, wind_mph=50.0, precipitation_pct=100.0)
    a = WeatherAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_indoor_sport_nhl_zero_regardless_of_weather():
    ctx = WeatherContext(sport="NHL", temperature_f=-10.0, wind_mph=50.0, precipitation_pct=100.0)
    a = WeatherAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_home_adv_delta_always_zero():
    ctx = WeatherContext(sport="NFL", temperature_f=0.0, wind_mph=30.0, precipitation_pct=100.0)
    a = WeatherAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_all_three_effects_sum():
    ctx = WeatherContext(sport="NFL", temperature_f=30.0, wind_mph=20.0, precipitation_pct=80.0)
    a = WeatherAdjuster.adjustment(ctx)
    wind_term = WIND_COEF["NFL"] * Decimal('10')  # 20 - 10
    cold_term = COLD_COEF["NFL"] * Decimal('10') / Decimal('10')  # (40-30)/10
    precip_term = PRECIP_COEF["NFL"] * Decimal('0.8')
    expected = (wind_term + cold_term + precip_term).quantize(Decimal('0.000001'))
    assert a.totals_delta == expected


def test_components_have_three_terms():
    ctx = WeatherContext(sport="MLB", temperature_f=30.0, wind_mph=15.0, precipitation_pct=10.0)
    a = WeatherAdjuster.adjustment(ctx)
    assert a.components["source"] == "weather"
    assert "wind_term" in a.components
    assert "cold_term" in a.components
    assert "precip_term" in a.components


def test_weather_context_frozen():
    ctx = WeatherContext(sport="MLB", temperature_f=70.0, wind_mph=5.0)
    with pytest.raises(Exception):
        ctx.wind_mph = 999.0
