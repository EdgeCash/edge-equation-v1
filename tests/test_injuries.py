import pytest
from decimal import Decimal

from edge_equation.context.injuries import (
    InjuriesContext,
    InjuriesAdjuster,
    INJURY_SPORT_SCALE,
)


def test_equal_impact_zero_delta():
    ctx = InjuriesContext(
        sport="NBA",
        home_injury_impact=Decimal('0.3'),
        away_injury_impact=Decimal('0.3'),
    )
    a = InjuriesAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_healthier_home_positive_delta():
    # Home healthy (0) vs Away banged up (0.5) -> positive home_adv
    ctx = InjuriesContext(
        sport="NBA",
        home_injury_impact=Decimal('0'),
        away_injury_impact=Decimal('0.5'),
    )
    a = InjuriesAdjuster.adjustment(ctx)
    expected = (Decimal('0.5') * INJURY_SPORT_SCALE["NBA"]).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_healthier_away_negative_delta():
    ctx = InjuriesContext(
        sport="NBA",
        home_injury_impact=Decimal('0.5'),
        away_injury_impact=Decimal('0'),
    )
    a = InjuriesAdjuster.adjustment(ctx)
    expected = (Decimal('-0.5') * INJURY_SPORT_SCALE["NBA"]).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_scale_larger_for_nba_than_nhl():
    assert INJURY_SPORT_SCALE["NBA"] > INJURY_SPORT_SCALE["NHL"]


def test_nfl_scale():
    ctx = InjuriesContext(
        sport="NFL",
        home_injury_impact=Decimal('0'),
        away_injury_impact=Decimal('0.25'),
    )
    a = InjuriesAdjuster.adjustment(ctx)
    expected = (Decimal('0.25') * INJURY_SPORT_SCALE["NFL"]).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_unknown_sport_zero_scale_zero_delta():
    ctx = InjuriesContext(
        sport="CRICKET",
        home_injury_impact=Decimal('0'),
        away_injury_impact=Decimal('0.9'),
    )
    a = InjuriesAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_totals_delta_always_zero():
    ctx = InjuriesContext(
        sport="NBA",
        home_injury_impact=Decimal('0.1'),
        away_injury_impact=Decimal('0.8'),
    )
    a = InjuriesAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_components_expose_impacts_and_scale():
    ctx = InjuriesContext(
        sport="NBA",
        home_injury_impact=Decimal('0.2'),
        away_injury_impact=Decimal('0.4'),
    )
    a = InjuriesAdjuster.adjustment(ctx)
    assert a.components["source"] == "injuries"
    assert a.components["home_impact"] == "0.2"
    assert a.components["away_impact"] == "0.4"
    assert a.components["scale"] == str(INJURY_SPORT_SCALE["NBA"])


def test_injuries_context_frozen():
    ctx = InjuriesContext(
        sport="NBA",
        home_injury_impact=Decimal('0.1'),
        away_injury_impact=Decimal('0.2'),
    )
    with pytest.raises(Exception):
        ctx.home_injury_impact = Decimal('0.9')


def test_default_impacts_are_zero():
    ctx = InjuriesContext(sport="NBA")
    a = InjuriesAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))
