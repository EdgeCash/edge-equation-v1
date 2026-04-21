from decimal import Decimal
import pytest

from edge_equation.persistence.db import Database
from edge_equation.persistence.realization_store import (
    RealizationStore,
    OutcomeRecord,
    VALID_OUTCOMES,
)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def test_record_and_fetch_win(conn):
    oid = RealizationStore.record_outcome(
        conn, "G1", "ML", "BOS", "win",
        actual_value=None, recorded_at="2026-04-20T22:00:00",
    )
    assert oid > 0
    rec = RealizationStore.get_outcome(conn, "G1", "ML", "BOS")
    assert rec is not None
    assert rec.outcome == "win"
    assert rec.actual_value is None


def test_record_with_actual_value(conn):
    RealizationStore.record_outcome(
        conn, "G1", "Total", "Over 9.5", "win",
        actual_value=Decimal('11'), recorded_at="2026-04-20T22:00:00",
    )
    rec = RealizationStore.get_outcome(conn, "G1", "Total", "Over 9.5")
    assert rec.actual_value == Decimal('11')


def test_invalid_outcome_raises(conn):
    with pytest.raises(ValueError, match="outcome must be"):
        RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "cancelled")


def test_valid_outcomes_list():
    assert set(VALID_OUTCOMES) == {"win", "loss", "push", "void"}


def test_upsert_on_duplicate_key(conn):
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")
    # Re-record with different outcome -- should upsert
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "loss")
    rec = RealizationStore.get_outcome(conn, "G1", "ML", "BOS")
    assert rec.outcome == "loss"


def test_multiple_markets_per_game(conn):
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")
    RealizationStore.record_outcome(conn, "G1", "Total", "Over 9.5", "loss")
    RealizationStore.record_outcome(conn, "G1", "Total", "Under 9.5", "win")
    rows = RealizationStore.list_outcomes_by_game(conn, "G1")
    assert len(rows) == 3
    outcomes = {(r.market_type, r.selection): r.outcome for r in rows}
    assert outcomes[("ML", "BOS")] == "win"
    assert outcomes[("Total", "Over 9.5")] == "loss"
    assert outcomes[("Total", "Under 9.5")] == "win"


def test_get_outcome_missing_returns_none(conn):
    assert RealizationStore.get_outcome(conn, "NO_GAME", "ML", "X") is None


def test_list_recent_orders_by_recorded_at_desc(conn):
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win", recorded_at="2026-04-20T22:00:00")
    RealizationStore.record_outcome(conn, "G2", "ML", "NYY", "loss", recorded_at="2026-04-21T22:00:00")
    RealizationStore.record_outcome(conn, "G3", "ML", "DET", "push", recorded_at="2026-04-19T22:00:00")
    rows = RealizationStore.list_recent(conn, limit=2)
    assert len(rows) == 2
    assert rows[0].recorded_at >= rows[1].recorded_at


def test_outcome_record_frozen(conn):
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")
    rec = RealizationStore.get_outcome(conn, "G1", "ML", "BOS")
    with pytest.raises(Exception):
        rec.outcome = "loss"


def test_outcome_record_to_dict(conn):
    RealizationStore.record_outcome(
        conn, "G1", "Total", "Over 8.5", "push",
        actual_value=Decimal('8.5'), recorded_at="2026-04-20T22:00:00",
    )
    rec = RealizationStore.get_outcome(conn, "G1", "Total", "Over 8.5")
    d = rec.to_dict()
    assert d["outcome"] == "push"
    assert d["actual_value"] == "8.5"
    assert d["market_type"] == "Total"


def test_all_four_outcome_values_accepted(conn):
    for i, outcome in enumerate(VALID_OUTCOMES):
        RealizationStore.record_outcome(conn, f"G{i}", "ML", "X", outcome)
    rows = RealizationStore.list_recent(conn, limit=10)
    assert len(rows) == 4
    assert {r.outcome for r in rows} == set(VALID_OUTCOMES)
