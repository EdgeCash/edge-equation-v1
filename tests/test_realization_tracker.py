from decimal import Decimal
import pytest

from edge_equation.engine.pick_schema import Pick, Line
from edge_equation.engine.realization import (
    RealizationTracker,
    SETTLED_WIN,
    SETTLED_LOSS,
    SETTLED_PUSH,
    SETTLED_VOID,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.realization_store import RealizationStore
from edge_equation.persistence.slate_store import SlateStore, SlateRecord


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _ml_pick(game_id, selection, grade="B") -> Pick:
    return Pick(
        sport="MLB",
        market_type="ML",
        selection=selection,
        line=Line(odds=-132),
        fair_prob=Decimal('0.55'),
        edge=Decimal('0.02'),
        kelly=Decimal('0.008'),
        grade=grade,
        realization=52,
        game_id=game_id,
    )


def test_realization_value_table():
    assert RealizationTracker.realization_value("win") == SETTLED_WIN
    assert RealizationTracker.realization_value("loss") == SETTLED_LOSS
    assert RealizationTracker.realization_value("push") == SETTLED_PUSH
    assert RealizationTracker.realization_value("void") == SETTLED_VOID


def test_realization_value_unknown_raises():
    with pytest.raises(ValueError, match="unknown outcome"):
        RealizationTracker.realization_value("cancelled")


def test_settle_picks_no_match_returns_zero(conn):
    PickStore.insert(conn, _ml_pick("G1", "BOS"))
    # No outcomes recorded
    result = RealizationTracker.settle_picks(conn)
    assert result == {"matched": 0, "updated": 0}


def test_settle_picks_matches_and_updates(conn):
    PickStore.insert(conn, _ml_pick("G1", "BOS"))
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")

    result = RealizationTracker.settle_picks(conn)
    assert result == {"matched": 1, "updated": 1}

    rec = PickStore.list_by_game(conn, "G1")[0]
    assert rec.realization == SETTLED_WIN


def test_settle_picks_idempotent(conn):
    PickStore.insert(conn, _ml_pick("G1", "BOS"))
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")

    RealizationTracker.settle_picks(conn)
    second = RealizationTracker.settle_picks(conn)
    # Matched stays the same; updated drops to 0 because values are already settled.
    assert second == {"matched": 1, "updated": 0}


def test_settle_picks_all_four_outcomes(conn):
    PickStore.insert(conn, _ml_pick("G1", "BOS"))
    PickStore.insert(conn, _ml_pick("G2", "NYY"))
    PickStore.insert(conn, _ml_pick("G3", "DET"))
    PickStore.insert(conn, _ml_pick("G4", "PHI"))
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")
    RealizationStore.record_outcome(conn, "G2", "ML", "NYY", "loss")
    RealizationStore.record_outcome(conn, "G3", "ML", "DET", "push")
    RealizationStore.record_outcome(conn, "G4", "ML", "PHI", "void")

    RealizationTracker.settle_picks(conn)

    by_game = {PickStore.list_by_game(conn, g)[0].realization for g in ("G1", "G2", "G3", "G4")}
    assert SETTLED_WIN in by_game
    assert SETTLED_LOSS in by_game
    assert SETTLED_PUSH in by_game
    assert SETTLED_VOID in by_game


def test_settle_picks_scoped_to_slate(conn):
    SlateStore.insert(conn, SlateRecord(
        slate_id="s1", generated_at="2026-04-20T09:00", sport=None, card_type="daily_edge",
    ))
    PickStore.insert(conn, _ml_pick("G1", "BOS"), slate_id="s1")
    PickStore.insert(conn, _ml_pick("G2", "NYY"))  # no slate
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")
    RealizationStore.record_outcome(conn, "G2", "ML", "NYY", "win")

    result = RealizationTracker.settle_picks(conn, slate_id="s1")
    assert result["matched"] == 1
    assert PickStore.list_by_game(conn, "G1")[0].realization == SETTLED_WIN
    # G2 unchanged because it's not in slate s1
    assert PickStore.list_by_game(conn, "G2")[0].realization == 52


def test_hit_rate_by_grade_empty(conn):
    assert RealizationTracker.hit_rate_by_grade(conn) == {}


def test_hit_rate_by_grade_computes_ratio(conn):
    # 3 A-grade picks, all settle: 2 wins 1 loss -> 2/3 = 0.666...
    for i in range(2):
        PickStore.insert(conn, _ml_pick(f"GW{i}", "HOME", grade="A"))
        RealizationStore.record_outcome(conn, f"GW{i}", "ML", "HOME", "win")
    PickStore.insert(conn, _ml_pick("GL0", "HOME", grade="A"))
    RealizationStore.record_outcome(conn, "GL0", "ML", "HOME", "loss")

    # 2 B-grade picks: 1 win 1 loss -> 0.5
    PickStore.insert(conn, _ml_pick("GB0", "HOME", grade="B"))
    RealizationStore.record_outcome(conn, "GB0", "ML", "HOME", "win")
    PickStore.insert(conn, _ml_pick("GB1", "HOME", grade="B"))
    RealizationStore.record_outcome(conn, "GB1", "ML", "HOME", "loss")

    RealizationTracker.settle_picks(conn)
    result = RealizationTracker.hit_rate_by_grade(conn)
    assert result["A"]["n"] == 3
    assert result["A"]["wins"] == 2
    assert abs(result["A"]["hit_rate"] - 2/3) < 1e-9
    assert result["B"]["n"] == 2
    assert abs(result["B"]["hit_rate"] - 0.5) < 1e-9


def test_hit_rate_by_grade_pushes_excluded_from_denominator(conn):
    # 1 win, 1 push -> hit_rate = 1 / (2-1) = 1.0
    PickStore.insert(conn, _ml_pick("GW", "HOME", grade="A"))
    PickStore.insert(conn, _ml_pick("GP", "HOME", grade="A"))
    RealizationStore.record_outcome(conn, "GW", "ML", "HOME", "win")
    RealizationStore.record_outcome(conn, "GP", "ML", "HOME", "push")
    RealizationTracker.settle_picks(conn)
    result = RealizationTracker.hit_rate_by_grade(conn)
    assert result["A"]["n"] == 2
    assert result["A"]["pushes"] == 1
    assert result["A"]["hit_rate"] == 1.0


def test_hit_rate_by_grade_filtered_by_sport(conn):
    PickStore.insert(conn, _ml_pick("G1", "BOS", grade="A"))  # MLB
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")
    # NHL pick same grade
    PickStore.insert(conn, Pick(
        sport="NHL", market_type="ML", selection="PIT", line=Line(odds=-110),
        grade="A", realization=59, game_id="GN1",
    ))
    RealizationStore.record_outcome(conn, "GN1", "ML", "PIT", "loss")
    RealizationTracker.settle_picks(conn)

    mlb_only = RealizationTracker.hit_rate_by_grade(conn, sport="MLB")
    assert mlb_only["A"]["n"] == 1
    assert mlb_only["A"]["wins"] == 1


def test_unsettled_picks_excluded_from_hit_rate(conn):
    PickStore.insert(conn, _ml_pick("GU", "BOS", grade="A"))  # never settled
    result = RealizationTracker.hit_rate_by_grade(conn)
    # No settled picks -> empty result
    assert result == {}
