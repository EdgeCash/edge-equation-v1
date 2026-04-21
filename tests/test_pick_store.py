from decimal import Decimal
import pytest

from edge_equation.engine.pick_schema import Pick, Line
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore, PickRecord
from edge_equation.persistence.slate_store import SlateStore, SlateRecord


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _make_ml_pick(**overrides) -> Pick:
    defaults = dict(
        sport="MLB",
        market_type="ML",
        selection="BOS",
        line=Line(odds=-132),
        fair_prob=Decimal('0.553412'),
        expected_value=None,
        edge=Decimal('0.022134'),
        kelly=Decimal('0.0085'),
        grade="B",
        realization=52,
        game_id="MLB-2026-04-20-DET-BOS",
        event_time="2026-04-20T13:05:00-04:00",
        decay_halflife_days=Decimal('277.258872'),
        hfa_value=Decimal('0.400000'),
        kelly_breakdown={"full_kelly": "0.05", "kelly_final": "0.0085", "capped": False},
        metadata={"source": "test"},
    )
    defaults.update(overrides)
    return Pick(**defaults)


def _make_total_pick() -> Pick:
    return Pick(
        sport="MLB",
        market_type="Total",
        selection="Over 9.5",
        line=Line(odds=-110, number=Decimal('9.5')),
        expected_value=Decimal('9.78'),
        grade="C",
        realization=47,
        game_id="MLB-2026-04-20-DET-BOS",
    )


def test_insert_returns_id(conn):
    pid = PickStore.insert(conn, _make_ml_pick())
    assert isinstance(pid, int)
    assert pid > 0


def test_insert_roundtrip_preserves_decimals(conn):
    pick = _make_ml_pick()
    pid = PickStore.insert(conn, pick, slate_id=None, recorded_at="2026-04-20T09:00:00")
    rec = PickStore.get(conn, pid)
    assert rec is not None
    assert rec.fair_prob == Decimal('0.553412')
    assert rec.edge == Decimal('0.022134')
    assert rec.kelly == Decimal('0.0085')
    assert rec.decay_halflife_days == Decimal('277.258872')
    assert rec.hfa_value == Decimal('0.400000')


def test_insert_roundtrip_with_total_line_number(conn):
    pid = PickStore.insert(conn, _make_total_pick())
    rec = PickStore.get(conn, pid)
    assert rec.line_number == Decimal('9.5')
    assert rec.expected_value == Decimal('9.78')
    assert rec.fair_prob is None


def test_to_pick_rehydrates_identically(conn):
    SlateStore.insert(conn, SlateRecord(
        slate_id="s1", generated_at="2026-04-20T09:00", sport=None, card_type="daily_edge",
    ))
    original = _make_ml_pick()
    pid = PickStore.insert(conn, original, slate_id="s1", recorded_at="2026-04-20T09:00:00")
    rec = PickStore.get(conn, pid)
    rebuilt = rec.to_pick()
    assert rebuilt.fair_prob == original.fair_prob
    assert rebuilt.edge == original.edge
    assert rebuilt.kelly == original.kelly
    assert rebuilt.grade == original.grade
    assert rebuilt.line.odds == original.line.odds
    assert rebuilt.metadata == original.metadata
    assert rebuilt.kelly_breakdown == original.kelly_breakdown


def test_kelly_breakdown_none_roundtrips(conn):
    pick = _make_ml_pick(kelly_breakdown=None)
    pid = PickStore.insert(conn, pick)
    rec = PickStore.get(conn, pid)
    assert rec.kelly_breakdown is None


def test_metadata_preserved(conn):
    pick = _make_ml_pick(metadata={"raw_universal_sum": "0.085", "note": "bigtime"})
    pid = PickStore.insert(conn, pick)
    rec = PickStore.get(conn, pid)
    assert rec.metadata["raw_universal_sum"] == "0.085"
    assert rec.metadata["note"] == "bigtime"


def test_insert_many_returns_list_of_ids(conn):
    SlateStore.insert(conn, SlateRecord(
        slate_id="slate_x", generated_at="2026-04-20T09:00", sport=None, card_type="daily_edge",
    ))
    picks = [_make_ml_pick(), _make_total_pick()]
    ids = PickStore.insert_many(conn, picks, slate_id="slate_x")
    assert len(ids) == 2
    assert ids[0] != ids[1]


def test_list_by_slate(conn):
    SlateStore.insert(conn, SlateRecord(
        slate_id="slate_a",
        generated_at="2026-04-20T09:00:00",
        sport="MLB",
        card_type="daily_edge",
    ))
    PickStore.insert(conn, _make_ml_pick(), slate_id="slate_a")
    PickStore.insert(conn, _make_total_pick(), slate_id="slate_a")
    PickStore.insert(conn, _make_ml_pick(selection="NYY"))  # no slate

    rows = PickStore.list_by_slate(conn, "slate_a")
    assert len(rows) == 2


def test_list_by_game(conn):
    PickStore.insert(conn, _make_ml_pick(game_id="GAME_A"))
    PickStore.insert(conn, _make_ml_pick(game_id="GAME_A", selection="DET"))
    PickStore.insert(conn, _make_ml_pick(game_id="GAME_B"))
    rows = PickStore.list_by_game(conn, "GAME_A")
    assert len(rows) == 2
    assert all(r.game_id == "GAME_A" for r in rows)


def test_list_by_sport_orders_recent_first(conn):
    PickStore.insert(conn, _make_ml_pick(sport="MLB"), recorded_at="2026-04-20T09:00:00")
    PickStore.insert(conn, _make_ml_pick(sport="MLB"), recorded_at="2026-04-21T09:00:00")
    PickStore.insert(conn, _make_ml_pick(sport="NHL", selection="PHI"), recorded_at="2026-04-21T09:00:00")
    mlb = PickStore.list_by_sport(conn, "MLB")
    assert len(mlb) == 2
    assert mlb[0].recorded_at >= mlb[1].recorded_at


def test_update_realization(conn):
    pid = PickStore.insert(conn, _make_ml_pick())
    n = PickStore.update_realization(conn, pid, 100)
    assert n == 1
    rec = PickStore.get(conn, pid)
    assert rec.realization == 100


def test_update_realization_missing_returns_zero(conn):
    n = PickStore.update_realization(conn, 9999, 100)
    assert n == 0


def test_pick_record_frozen(conn):
    pid = PickStore.insert(conn, _make_ml_pick())
    rec = PickStore.get(conn, pid)
    with pytest.raises(Exception):
        rec.pick_id = 9999


def test_pick_record_to_dict_has_db_fields(conn):
    SlateStore.insert(conn, SlateRecord(
        slate_id="slate_z", generated_at="2026-04-20T09:00", sport=None, card_type="daily_edge",
    ))
    pid = PickStore.insert(conn, _make_ml_pick(), slate_id="slate_z")
    rec = PickStore.get(conn, pid)
    d = rec.to_dict()
    assert d["pick_id"] == pid
    assert d["slate_id"] == "slate_z"
    assert d["sport"] == "MLB"


def test_get_missing_returns_none(conn):
    assert PickStore.get(conn, 9999) is None
