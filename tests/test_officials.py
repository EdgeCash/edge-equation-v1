import pytest
from decimal import Decimal

from edge_equation.context.officials import OfficialsContext, OfficialsAdjuster


def test_zero_delta_passthrough():
    ctx = OfficialsContext(sport="NBA", crew_id="CREW_A", crew_total_delta=Decimal('0'))
    a = OfficialsAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_positive_delta_passthrough():
    ctx = OfficialsContext(sport="NBA", crew_id="CREW_A", crew_total_delta=Decimal('1.25'))
    a = OfficialsAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('1.25').quantize(Decimal('0.000001'))


def test_negative_delta_passthrough():
    ctx = OfficialsContext(sport="MLB", crew_id="UMP_B", crew_total_delta=Decimal('-0.85'))
    a = OfficialsAdjuster.adjustment(ctx)
    assert a.totals_delta == Decimal('-0.85').quantize(Decimal('0.000001'))


def test_home_adv_delta_always_zero():
    ctx = OfficialsContext(sport="NBA", crew_id="X", crew_total_delta=Decimal('10'))
    a = OfficialsAdjuster.adjustment(ctx)
    assert a.home_adv_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_optional_crew_id_defaults_none():
    ctx = OfficialsContext(sport="NFL")
    a = OfficialsAdjuster.adjustment(ctx)
    assert a.components["crew_id"] is None
    assert a.totals_delta == Decimal('0').quantize(Decimal('0.000001'))


def test_components_include_crew_id_and_delta():
    ctx = OfficialsContext(sport="NBA", crew_id="CREW_Z", crew_total_delta=Decimal('0.50'))
    a = OfficialsAdjuster.adjustment(ctx)
    assert a.components["source"] == "officials"
    assert a.components["crew_id"] == "CREW_Z"
    assert a.components["crew_total_delta"] == "0.50"


def test_officials_context_frozen():
    ctx = OfficialsContext(sport="NBA", crew_id="A", crew_total_delta=Decimal('1'))
    with pytest.raises(Exception):
        ctx.crew_total_delta = Decimal('9')
