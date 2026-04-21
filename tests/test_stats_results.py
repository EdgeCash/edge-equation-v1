from decimal import Decimal
import pytest

from edge_equation.persistence.db import Database
from edge_equation.stats.results import (
    GameResult,
    GameResultsStore,
    VALID_STATUS,
)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _r(game_id, league="KBO", home="Doosan Bears", away="LG Twins",
       home_score=5, away_score=3, start="2026-04-13T18:30:00+09:00",
       status="final"):
    return GameResult(
        result_id=None,
        game_id=game_id, league=league,
        home_team=home, away_team=away,
        start_time=start,
        home_score=home_score, away_score=away_score,
        status=status,
    )


def test_game_result_helpers():
    g = _r("G1", home_score=5, away_score=3)
    assert g.home_won() is True
    assert g.is_draw() is False
    assert g.total() == 8
    assert g.margin() == 2


def test_game_result_draw():
    g = _r("G1", home_score=4, away_score=4)
    assert g.is_draw() is True
    assert g.home_won() is False


def test_record_and_get(conn):
    rid = GameResultsStore.record(conn, _r("G1"))
    assert rid > 0
    got = GameResultsStore.get(conn, "G1")
    assert got is not None
    assert got.home_score == 5


def test_record_upserts_on_game_id(conn):
    GameResultsStore.record(conn, _r("G1", home_score=5, away_score=3))
    GameResultsStore.record(conn, _r("G1", home_score=9, away_score=1))
    got = GameResultsStore.get(conn, "G1")
    assert got.home_score == 9
    assert got.away_score == 1
    # Only one row
    rows = conn.execute("SELECT COUNT(*) AS c FROM game_results").fetchone()
    assert int(rows["c"]) == 1


def test_record_invalid_status_raises(conn):
    with pytest.raises(ValueError, match="status must be"):
        GameResultsStore.record(conn, _r("G1", status="pending"))


def test_valid_status_list():
    assert "final" in VALID_STATUS
    assert "forfeit" in VALID_STATUS
    assert "suspended" in VALID_STATUS


def test_record_many(conn):
    ids = GameResultsStore.record_many(conn, [_r("G1"), _r("G2"), _r("G3")])
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_list_by_league_orders_recent_first(conn):
    GameResultsStore.record(conn, _r("G1", start="2026-04-13T18:30:00+09:00"))
    GameResultsStore.record(conn, _r("G2", start="2026-04-15T18:30:00+09:00"))
    GameResultsStore.record(conn, _r("G3", start="2026-04-14T18:30:00+09:00"))
    rows = GameResultsStore.list_by_league(conn, "KBO")
    assert len(rows) == 3
    assert rows[0].game_id == "G2"
    assert rows[1].game_id == "G3"
    assert rows[2].game_id == "G1"


def test_list_for_team_covers_home_and_away(conn):
    GameResultsStore.record(conn, _r("G1", home="Doosan Bears", away="LG Twins"))
    GameResultsStore.record(conn, _r("G2", home="LG Twins", away="Doosan Bears"))
    GameResultsStore.record(conn, _r("G3", home="LG Twins", away="KIA Tigers"))
    rows = GameResultsStore.list_for_team(conn, "KBO", "Doosan Bears")
    assert len(rows) == 2
    assert {r.game_id for r in rows} == {"G1", "G2"}


def test_list_between_inclusive_lower_exclusive_upper(conn):
    GameResultsStore.record(conn, _r("G1", start="2026-04-13T00:00:00+09:00"))
    GameResultsStore.record(conn, _r("G2", start="2026-04-14T00:00:00+09:00"))
    GameResultsStore.record(conn, _r("G3", start="2026-04-15T00:00:00+09:00"))
    rows = GameResultsStore.list_between(
        conn, "KBO", "2026-04-13T00:00:00+09:00", "2026-04-15T00:00:00+09:00",
    )
    assert len(rows) == 2
    assert {r.game_id for r in rows} == {"G1", "G2"}


def test_count_by_league(conn):
    GameResultsStore.record(conn, _r("G1", league="KBO"))
    GameResultsStore.record(conn, _r("G2", league="KBO"))
    GameResultsStore.record(conn, _r("G3", league="NPB"))
    assert GameResultsStore.count_by_league(conn, "KBO") == 2
    assert GameResultsStore.count_by_league(conn, "NPB") == 1
    assert GameResultsStore.count_by_league(conn, "MLB") == 0


def test_game_result_frozen():
    g = _r("G1")
    with pytest.raises(Exception):
        g.home_score = 999


def test_to_dict_shape(conn):
    GameResultsStore.record(conn, _r("G1"))
    got = GameResultsStore.get(conn, "G1")
    d = got.to_dict()
    assert d["game_id"] == "G1"
    assert d["home_team"] == "Doosan Bears"
    assert d["status"] == "final"
