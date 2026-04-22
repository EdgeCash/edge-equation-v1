"""Phase 20 posting cadence tests."""
import pytest

from edge_equation.posting.cadence import (
    CADENCE_BY_CARD_TYPE,
    CADENCE_WINDOWS,
    CARD_TYPE_DAILY_EDGE,
    CARD_TYPE_EVENING_EDGE,
    CARD_TYPE_LEDGER,
    CARD_TYPE_OVERSEAS_EDGE,
    CARD_TYPE_SPOTLIGHT,
    CENTRAL_TZ,
    CadenceSlot,
    is_mandatory,
    slot_for,
)


def test_five_windows_defined():
    assert len(CADENCE_WINDOWS) == 5


def test_windows_cover_required_card_types():
    types = {s.card_type for s in CADENCE_WINDOWS}
    assert types == {
        CARD_TYPE_LEDGER,
        CARD_TYPE_DAILY_EDGE,
        CARD_TYPE_SPOTLIGHT,
        CARD_TYPE_EVENING_EDGE,
        CARD_TYPE_OVERSEAS_EDGE,
    }


def test_hours_match_spec():
    slots = {s.card_type: s for s in CADENCE_WINDOWS}
    assert slots[CARD_TYPE_LEDGER].hour_ct == 9
    assert slots[CARD_TYPE_DAILY_EDGE].hour_ct == 11
    assert slots[CARD_TYPE_SPOTLIGHT].hour_ct == 16
    assert slots[CARD_TYPE_EVENING_EDGE].hour_ct == 18
    assert slots[CARD_TYPE_OVERSEAS_EDGE].hour_ct == 23


def test_all_slots_are_central_timezone():
    assert CENTRAL_TZ == "America/Chicago"


def test_windows_ordered_chronologically():
    hours = [s.hour_ct for s in CADENCE_WINDOWS]
    assert hours == sorted(hours)


def test_slot_for_known_returns_slot():
    slot = slot_for(CARD_TYPE_DAILY_EDGE)
    assert isinstance(slot, CadenceSlot)
    assert slot.hour_ct == 11


def test_slot_for_unknown_returns_none():
    assert slot_for("not_a_card") is None


def test_is_mandatory():
    for ct in (CARD_TYPE_LEDGER, CARD_TYPE_DAILY_EDGE, CARD_TYPE_SPOTLIGHT,
               CARD_TYPE_EVENING_EDGE, CARD_TYPE_OVERSEAS_EDGE):
        assert is_mandatory(ct) is True
    for ct in ("model_highlight", "multi_leg_projection", "random_string"):
        assert is_mandatory(ct) is False


def test_cadence_slot_frozen():
    slot = CADENCE_WINDOWS[0]
    with pytest.raises(Exception):
        slot.hour_ct = 99


def test_cadence_slot_to_dict():
    slot = CADENCE_WINDOWS[0]
    d = slot.to_dict()
    assert d["card_type"] == CARD_TYPE_LEDGER
    assert d["hour_ct"] == 9


def test_cadence_by_card_type_map_matches_windows():
    assert len(CADENCE_BY_CARD_TYPE) == 5
    for slot in CADENCE_WINDOWS:
        assert CADENCE_BY_CARD_TYPE[slot.card_type] is slot
