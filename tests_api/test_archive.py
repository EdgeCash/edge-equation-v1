from datetime import datetime
from decimal import Decimal
import pytest

from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.engine.realization import RealizationTracker
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.realization_store import RealizationStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "archive.db"))


def _seed_slate(conn, slate_id="daily_20260420", card_type="daily_edge",
                grades=("A", "A", "B", "C"), generated_at="2026-04-20T09:00:00"):
    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id, generated_at=generated_at,
        sport=None, card_type=card_type, metadata={"leagues": ["MLB"]},
    ))
    for i, grade in enumerate(grades):
        PickStore.insert(conn, Pick(
            sport="MLB", market_type="ML", selection=f"Team_{i}",
            line=Line(odds=-132),
            fair_prob=Decimal('0.55'),
            edge=Decimal('0.02'), kelly=Decimal('0.008'),
            grade=grade, realization=52,
            game_id=f"G{i}",
        ), slate_id=slate_id)


def test_list_slates_empty(client):
    r = client.get("/archive/slates")
    assert r.status_code == 200
    assert r.json() == []


def test_list_slates_returns_recent(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "x.db"))
    conn = Database.open(str(tmp_path / "x.db"))
    Database.migrate(conn)
    _seed_slate(conn, slate_id="daily_20260420", generated_at="2026-04-20T09:00:00")
    _seed_slate(conn, slate_id="evening_20260420", card_type="evening_edge",
                generated_at="2026-04-20T19:00:00")
    conn.close()

    r = client.get("/archive/slates")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    # Ordered newest first
    assert body[0]["slate_id"] == "evening_20260420"
    assert body[1]["slate_id"] == "daily_20260420"
    assert body[0]["n_picks"] == 4


def test_list_slates_card_type_filter(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "x.db"))
    conn = Database.open(str(tmp_path / "x.db"))
    Database.migrate(conn)
    _seed_slate(conn, slate_id="daily_20260420")
    _seed_slate(conn, slate_id="evening_20260420", card_type="evening_edge")
    conn.close()

    r = client.get("/archive/slates", params={"card_type": "daily_edge"})
    body = r.json()
    assert len(body) == 1
    assert body[0]["slate_id"] == "daily_20260420"


def test_list_slates_rejects_bad_card_type(client):
    r = client.get("/archive/slates", params={"card_type": "garbage"})
    assert r.status_code == 400


def test_list_slates_respects_limit(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "x.db"))
    conn = Database.open(str(tmp_path / "x.db"))
    Database.migrate(conn)
    for i in range(5):
        _seed_slate(conn, slate_id=f"daily_{i}", generated_at=f"2026-04-2{i}T09:00:00")
    conn.close()

    r = client.get("/archive/slates", params={"limit": 2})
    assert len(r.json()) == 2


def test_latest_slate_not_found_returns_404(client):
    r = client.get("/archive/slates/latest", params={"card_type": "daily_edge"})
    assert r.status_code == 404


def test_latest_slate_returns_newest(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "x.db"))
    conn = Database.open(str(tmp_path / "x.db"))
    Database.migrate(conn)
    _seed_slate(conn, slate_id="daily_older", generated_at="2026-04-19T09:00:00")
    _seed_slate(conn, slate_id="daily_newer", generated_at="2026-04-20T09:00:00")
    conn.close()

    r = client.get("/archive/slates/latest", params={"card_type": "daily_edge"})
    body = r.json()
    assert r.status_code == 200
    assert body["slate_id"] == "daily_newer"
    assert len(body["picks"]) == 4


def test_get_slate_returns_picks(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "x.db"))
    conn = Database.open(str(tmp_path / "x.db"))
    Database.migrate(conn)
    _seed_slate(conn, slate_id="daily_20260420")
    conn.close()

    r = client.get("/archive/slates/daily_20260420")
    assert r.status_code == 200
    body = r.json()
    assert body["slate_id"] == "daily_20260420"
    assert len(body["picks"]) == 4
    assert all("grade" in p for p in body["picks"])


def test_get_slate_missing_returns_404(client):
    r = client.get("/archive/slates/no_such_slate")
    assert r.status_code == 404


def test_hit_rate_empty(client):
    r = client.get("/archive/hit-rate")
    assert r.status_code == 200
    assert r.json() == {"sport": None, "by_grade": {}}


def test_hit_rate_reports_settled_grades(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "x.db"))
    conn = Database.open(str(tmp_path / "x.db"))
    Database.migrate(conn)
    _seed_slate(conn, slate_id="daily_20260420", grades=("A", "A", "B", "C"))
    # Settle some outcomes
    outcomes = [
        ("G0", "ML", "Team_0", "win"),
        ("G1", "ML", "Team_1", "win"),
        ("G2", "ML", "Team_2", "loss"),
        ("G3", "ML", "Team_3", "loss"),
    ]
    for g, m, s, o in outcomes:
        RealizationStore.record_outcome(conn, g, m, s, o)
    RealizationTracker.settle_picks(conn)
    conn.close()

    r = client.get("/archive/hit-rate")
    body = r.json()
    assert body["by_grade"]["A"]["n"] == 2
    assert body["by_grade"]["A"]["wins"] == 2
    assert body["by_grade"]["A"]["hit_rate"] == 1.0
    assert body["by_grade"]["B"]["hit_rate"] == 0.0
    assert body["by_grade"]["C"]["hit_rate"] == 0.0


def test_hit_rate_sport_filter(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "x.db"))
    conn = Database.open(str(tmp_path / "x.db"))
    Database.migrate(conn)
    _seed_slate(conn, slate_id="daily_20260420")
    conn.close()

    r_mlb = client.get("/archive/hit-rate", params={"sport": "MLB"})
    assert r_mlb.status_code == 200
    assert r_mlb.json()["sport"] == "MLB"

    r_nfl = client.get("/archive/hit-rate", params={"sport": "NFL"})
    assert r_nfl.json()["by_grade"] == {}
