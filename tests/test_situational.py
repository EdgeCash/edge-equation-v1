import pytest
from decimal import Decimal

from edge_equation.context.situational import (
    SituationalContext,
    SituationalAdjuster,
    B2B_PENALTY,
    LOOK_AHEAD_PENALTY,
    REVENGE_BONUS,
)


def test_no_flags_zero_delta():
    ctx = SituationalContext(sport="NBA")
    a = SituationalAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_home_b2b_penalty():
    ctx = SituationalContext(sport="NBA", home_b2b=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.home_adv_delta == B2B_PENALTY["NBA"].quantize(Decimal('0.000001'))


def test_away_b2b_helps_home():
    ctx = SituationalContext(sport="NBA", away_b2b=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.home_adv_delta == (-B2B_PENALTY["NBA"]).quantize(Decimal('0.000001'))


def test_both_b2b_cancel():
    ctx = SituationalContext(sport="NBA", home_b2b=True, away_b2b=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_home_look_ahead_penalty():
    ctx = SituationalContext(sport="NBA", home_look_ahead=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.home_adv_delta == LOOK_AHEAD_PENALTY.quantize(Decimal('0.000001'))


def test_away_look_ahead_helps_home():
    ctx = SituationalContext(sport="NBA", away_look_ahead=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.home_adv_delta == (-LOOK_AHEAD_PENALTY).quantize(Decimal('0.000001'))


def test_home_revenge_bonus():
    ctx = SituationalContext(sport="NBA", home_revenge=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.home_adv_delta == REVENGE_BONUS.quantize(Decimal('0.000001'))


def test_away_revenge_penalty_to_home():
    ctx = SituationalContext(sport="NBA", away_revenge=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.home_adv_delta == (-REVENGE_BONUS).quantize(Decimal('0.000001'))


def test_all_home_flags_sum():
    ctx = SituationalContext(
        sport="NBA",
        home_b2b=True,
        home_look_ahead=True,
        home_revenge=True,
    )
    a = SituationalAdjuster.adjustment(ctx)
    expected = (B2B_PENALTY["NBA"] + LOOK_AHEAD_PENALTY + REVENGE_BONUS).quantize(Decimal('0.000001'))
    assert a.home_adv_delta == expected


def test_unknown_sport_b2b_zero_but_others_apply():
    ctx = SituationalContext(
        sport="CRICKET",
        home_b2b=True,
        home_revenge=True,
    )
    a = SituationalAdjuster.adjustment(ctx)
    # b2b: 0 for unknown sport; revenge bonus still applies
    assert a.home_adv_delta == REVENGE_BONUS.quantize(Decimal('0.000001'))


def test_totals_delta_always_zero():
    ctx = SituationalContext(sport="NBA", home_b2b=True, away_look_ahead=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_components_echo_flags():
    ctx = SituationalContext(sport="NBA", home_b2b=True, away_revenge=True)
    a = SituationalAdjuster.adjustment(ctx)
    assert a.components["source"] == "situational"
    assert a.components["home_b2b"] is True
    assert a.components["away_revenge"] is True


def test_situational_context_frozen():
    ctx = SituationalContext(sport="NBA", home_b2b=True)
    with pytest.raises(Exception):
        ctx.home_b2b = False
