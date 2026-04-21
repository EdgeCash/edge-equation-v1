import pytest
from decimal import Decimal

from edge_equation.context.adjustment import ContextAdjustment
from edge_equation.context.registry import ContextBundle, ContextRegistry
from edge_equation.context.rest import RestContext, RestAdjuster
from edge_equation.context.travel import TravelContext, TravelAdjuster
from edge_equation.context.weather import WeatherContext, WeatherAdjuster
from edge_equation.context.officials import OfficialsContext, OfficialsAdjuster
from edge_equation.context.situational import SituationalContext, SituationalAdjuster
from edge_equation.context.injuries import InjuriesContext, InjuriesAdjuster


def test_empty_bundle_zero_adjustment():
    a = ContextRegistry.compose(ContextBundle())
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))
    assert a.components == {}


def test_rest_only_bundle_matches_standalone():
    rest = RestContext(sport="NBA", home_rest_days=2, away_rest_days=0)
    bundle = ContextBundle(rest=rest)
    composed = ContextRegistry.compose(bundle)
    direct = RestAdjuster.adjustment(rest)
    assert composed.home_adv_delta == direct.home_adv_delta
    assert "rest" in composed.components


def test_sum_of_all_sources_matches_manual_sum():
    rest = RestContext(sport="NBA", home_rest_days=3, away_rest_days=0)
    travel = TravelContext(sport="NBA", away_travel_miles=1500.0, timezone_change_hours=2)
    weather = WeatherContext(sport="MLB", temperature_f=30.0, wind_mph=20.0, precipitation_pct=20.0)
    officials = OfficialsContext(sport="NBA", crew_id="CREW_X", crew_total_delta=Decimal('0.30'))
    situational = SituationalContext(sport="NBA", home_b2b=True, away_look_ahead=True)
    injuries = InjuriesContext(
        sport="NBA",
        home_injury_impact=Decimal('0.1'),
        away_injury_impact=Decimal('0.4'),
    )
    bundle = ContextBundle(
        rest=rest,
        travel=travel,
        weather=weather,
        officials=officials,
        situational=situational,
        injuries=injuries,
    )
    composed = ContextRegistry.compose(bundle)

    expected_home = (
        RestAdjuster.adjustment(rest).home_adv_delta
        + TravelAdjuster.adjustment(travel).home_adv_delta
        + WeatherAdjuster.adjustment(weather).home_adv_delta
        + OfficialsAdjuster.adjustment(officials).home_adv_delta
        + SituationalAdjuster.adjustment(situational).home_adv_delta
        + InjuriesAdjuster.adjustment(injuries).home_adv_delta
    )
    expected_totals = (
        RestAdjuster.adjustment(rest).totals_delta
        + TravelAdjuster.adjustment(travel).totals_delta
        + WeatherAdjuster.adjustment(weather).totals_delta
        + OfficialsAdjuster.adjustment(officials).totals_delta
        + SituationalAdjuster.adjustment(situational).totals_delta
        + InjuriesAdjuster.adjustment(injuries).totals_delta
    )
    assert composed.home_adv_delta == expected_home.quantize(Decimal('0.000001'))
    assert composed.totals_delta == expected_totals.quantize(Decimal('0.000001'))


def test_components_include_every_active_source():
    bundle = ContextBundle(
        rest=RestContext(sport="NBA", home_rest_days=2, away_rest_days=1),
        travel=TravelContext(sport="NBA", away_travel_miles=500.0, timezone_change_hours=0),
        weather=WeatherContext(sport="MLB"),
        officials=OfficialsContext(sport="NBA", crew_total_delta=Decimal('0.1')),
        situational=SituationalContext(sport="NBA"),
        injuries=InjuriesContext(sport="NBA"),
    )
    composed = ContextRegistry.compose(bundle)
    assert set(composed.components.keys()) == {
        "rest", "travel", "weather", "officials", "situational", "injuries",
    }


def test_inactive_sources_do_not_appear_in_components():
    bundle = ContextBundle(
        rest=RestContext(sport="NBA", home_rest_days=1, away_rest_days=1),
    )
    composed = ContextRegistry.compose(bundle)
    assert set(composed.components.keys()) == {"rest"}


def test_context_bundle_frozen():
    bundle = ContextBundle()
    with pytest.raises(Exception):
        bundle.rest = RestContext(sport="NBA", home_rest_days=1, away_rest_days=1)


def test_context_adjustment_to_dict_has_string_deltas():
    bundle = ContextBundle(
        officials=OfficialsContext(sport="NBA", crew_total_delta=Decimal('0.75')),
    )
    composed = ContextRegistry.compose(bundle)
    d = composed.to_dict()
    assert d["totals_delta"] == "0.750000"
    assert isinstance(d["home_adv_delta"], str)
    assert "officials" in d["components"]


def test_returns_context_adjustment_type():
    a = ContextRegistry.compose(ContextBundle())
    assert isinstance(a, ContextAdjustment)
