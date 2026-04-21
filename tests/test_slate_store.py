from datetime import datetime
import pytest

from edge_equation.persistence.db import Database
from edge_equation.persistence.slate_store import SlateStore, SlateRecord


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def test_insert_and_get_roundtrip(conn):
    slate = SlateRecord(
        slate_id="daily_20260420_morning",
        generated_at="2026-04-20T09:00:00",
        sport="MLB",
        card_type="daily_edge",
        metadata={"public_mode": False, "note": "spring slate"},
    )
    SlateStore.insert(conn, slate)
    fetched = SlateStore.get(conn, "daily_20260420_morning")
    assert fetched is not None
    assert fetched.slate_id == slate.slate_id
    assert fetched.sport == "MLB"
    assert fetched.metadata["note"] == "spring slate"


def test_get_missing_returns_none(conn):
    assert SlateStore.get(conn, "no_such_slate") is None


def test_insert_accepts_datetime_generated_at(conn):
    slate = SlateRecord(
        slate_id="dt_test",
        generated_at=datetime(2026, 4, 20, 9, 0, 0),
        sport=None,
        card_type="daily_edge",
    )
    SlateStore.insert(conn, slate)
    fetched = SlateStore.get(conn, "dt_test")
    assert fetched.generated_at == "2026-04-20T09:00:00"


def test_empty_metadata_handled(conn):
    slate = SlateRecord(
        slate_id="no_meta",
        generated_at="2026-04-20T09:00:00",
        sport=None,
        card_type="evening_edge",
    )
    SlateStore.insert(conn, slate)
    fetched = SlateStore.get(conn, "no_meta")
    assert fetched.metadata == {}


def test_list_recent_ordered_desc(conn):
    for i in range(5):
        SlateStore.insert(conn, SlateRecord(
            slate_id=f"slate_{i}",
            generated_at=f"2026-04-2{i}T09:00:00",
            sport="MLB",
            card_type="daily_edge",
        ))
    rows = SlateStore.list_recent(conn, limit=3)
    assert len(rows) == 3
    assert rows[0].slate_id == "slate_4"
    assert rows[1].slate_id == "slate_3"


def test_list_by_card_type_filters(conn):
    for i in range(3):
        SlateStore.insert(conn, SlateRecord(
            slate_id=f"daily_{i}",
            generated_at=f"2026-04-2{i}T09:00:00",
            sport="MLB",
            card_type="daily_edge",
        ))
    for i in range(2):
        SlateStore.insert(conn, SlateRecord(
            slate_id=f"evening_{i}",
            generated_at=f"2026-04-2{i}T19:00:00",
            sport="MLB",
            card_type="evening_edge",
        ))
    daily = SlateStore.list_by_card_type(conn, "daily_edge")
    evening = SlateStore.list_by_card_type(conn, "evening_edge")
    assert len(daily) == 3
    assert len(evening) == 2


def test_delete_removes_row(conn):
    SlateStore.insert(conn, SlateRecord(
        slate_id="to_delete",
        generated_at="2026-04-20T09:00:00",
        sport=None,
        card_type="daily_edge",
    ))
    assert SlateStore.get(conn, "to_delete") is not None
    n = SlateStore.delete(conn, "to_delete")
    assert n == 1
    assert SlateStore.get(conn, "to_delete") is None


def test_slate_record_frozen():
    slate = SlateRecord(
        slate_id="x",
        generated_at="2026-04-20T09:00:00",
        sport=None,
        card_type="daily_edge",
    )
    with pytest.raises(Exception):
        slate.slate_id = "hacked"


def test_slate_record_to_dict_shape():
    slate = SlateRecord(
        slate_id="x",
        generated_at="2026-04-20T09:00:00",
        sport="MLB",
        card_type="daily_edge",
        metadata={"k": 1},
    )
    d = slate.to_dict()
    assert d["slate_id"] == "x"
    assert d["metadata"] == {"k": 1}
