"""
Phase 8 end-to-end integration:
- Generate picks via the existing engine
- Persist a Slate + Picks in SQLite
- Record outcomes
- Settle picks via RealizationTracker
- Compute hit-rate by grade
- Exercise OddsCache alongside the pick flow (simulated API payload)
"""
from datetime import datetime, timedelta
from decimal import Decimal
import pytest

from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.pick_schema import Line
from edge_equation.engine.realization import RealizationTracker, SETTLED_WIN, SETTLED_LOSS
from edge_equation.persistence.db import Database
from edge_equation.persistence.slate_store import SlateStore, SlateRecord
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.odds_cache import OddsCache
from edge_equation.persistence.realization_store import RealizationStore


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _build_and_pick(sport: str, game_id: str, selection: str, strength_home=1.4, strength_away=1.1, odds=-132):
    bundle = FeatureBuilder.build(
        sport=sport,
        market_type="ML",
        inputs={"strength_home": strength_home, "strength_away": strength_away, "home_adv": 0.115},
        universal_features={"home_edge": 0.05},
        game_id=game_id,
        selection=selection,
    )
    return BettingEngine.evaluate(bundle, Line(odds=odds))


def test_full_round_trip_single_slate_all_wins(conn):
    slate_id = "daily_20260420_morning"
    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id,
        generated_at="2026-04-20T09:00:00",
        sport="MLB",
        card_type="daily_edge",
        metadata={"public_mode": False},
    ))

    picks = [
        _build_and_pick("MLB", "G1", "BOS"),
        _build_and_pick("MLB", "G2", "NYY", strength_home=1.5, strength_away=1.0, odds=-150),
        _build_and_pick("MLB", "G3", "DET", strength_home=1.45, strength_away=1.05, odds=-140),
    ]
    ids = PickStore.insert_many(conn, picks, slate_id=slate_id, recorded_at="2026-04-20T09:05:00")
    assert len(ids) == 3

    # Fake outcomes come in that evening
    for g, sel in [("G1", "BOS"), ("G2", "NYY"), ("G3", "DET")]:
        RealizationStore.record_outcome(
            conn, g, "ML", sel, "win",
            recorded_at="2026-04-20T23:00:00",
        )

    result = RealizationTracker.settle_picks(conn, slate_id=slate_id)
    assert result["matched"] == 3
    assert result["updated"] == 3

    # Every pick now has realization=100
    for pid in ids:
        rec = PickStore.get(conn, pid)
        assert rec.realization == SETTLED_WIN


def test_hit_rate_report_mixed_outcomes(conn):
    picks = [
        _build_and_pick("MLB", "G1", "BOS"),
        _build_and_pick("MLB", "G2", "NYY"),
        _build_and_pick("MLB", "G3", "DET"),
        _build_and_pick("MLB", "G4", "PHI"),
    ]
    PickStore.insert_many(conn, picks)

    outcomes = [
        ("G1", "ML", "BOS", "win"),
        ("G2", "ML", "NYY", "win"),
        ("G3", "ML", "DET", "loss"),
        ("G4", "ML", "PHI", "win"),
    ]
    for g, m, s, o in outcomes:
        RealizationStore.record_outcome(conn, g, m, s, o)

    RealizationTracker.settle_picks(conn)
    report = RealizationTracker.hit_rate_by_grade(conn, sport="MLB")
    # All picks share the same grade (depends on edge), just check total rolls up
    total_n = sum(g["n"] for g in report.values())
    total_wins = sum(g["wins"] for g in report.values())
    assert total_n == 4
    assert total_wins == 3


def test_odds_cache_flow_alongside_picks(conn):
    # Cache a simulated API payload
    now = datetime(2026, 4, 20, 9, 0, 0)
    payload = {
        "sport": "baseball_mlb",
        "games": [
            {"id": "G1", "home_team": "BOS", "away_team": "DET",
             "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"price": -132}, {"price": 112}]}]}]},
        ],
    }
    OddsCache.put(conn, "theoddsapi:baseball_mlb:h2h:us", payload, ttl_seconds=900, now=now)

    # Second "API call" within TTL hits the cache
    still_fresh = OddsCache.get(conn, "theoddsapi:baseball_mlb:h2h:us", now=now + timedelta(seconds=300))
    assert still_fresh == payload

    # Write a pick sourced from that payload
    pick = _build_and_pick("MLB", "G1", "BOS")
    pid = PickStore.insert(conn, pick)
    rec = PickStore.get(conn, pid)
    assert rec.game_id == "G1"

    # After TTL the cache returns None but pick persists
    stale = OddsCache.get(conn, "theoddsapi:baseball_mlb:h2h:us", now=now + timedelta(hours=1))
    assert stale is None
    assert PickStore.get(conn, pid) is not None


def test_outcome_update_after_initial_settlement(conn):
    # A pick settles as a win; later the outcome gets corrected to a loss.
    PickStore.insert(conn, _build_and_pick("MLB", "G1", "BOS"))
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")
    RealizationTracker.settle_picks(conn)
    assert PickStore.list_by_game(conn, "G1")[0].realization == SETTLED_WIN

    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "loss")
    result = RealizationTracker.settle_picks(conn)
    assert result["updated"] == 1
    assert PickStore.list_by_game(conn, "G1")[0].realization == SETTLED_LOSS


def test_slate_metadata_survives_pick_insertion(conn):
    slate_id = "evening_20260420"
    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id,
        generated_at="2026-04-20T19:00:00",
        sport="MLB",
        card_type="evening_edge",
        metadata={"filters": ["ML", "Total"], "region": "us"},
    ))
    PickStore.insert(conn, _build_and_pick("MLB", "G1", "BOS"), slate_id=slate_id)
    slate = SlateStore.get(conn, slate_id)
    assert slate.metadata["filters"] == ["ML", "Total"]
    assert slate.metadata["region"] == "us"
    picks = PickStore.list_by_slate(conn, slate_id)
    assert len(picks) == 1


def test_realization_tracker_ignores_picks_without_outcome(conn):
    PickStore.insert(conn, _build_and_pick("MLB", "G1", "BOS"))
    PickStore.insert(conn, _build_and_pick("MLB", "G2", "NYY"))
    # Only G1 has an outcome
    RealizationStore.record_outcome(conn, "G1", "ML", "BOS", "win")
    result = RealizationTracker.settle_picks(conn)
    assert result["matched"] == 1
    # G2 retains its forecast realization
    rec = PickStore.list_by_game(conn, "G2")[0]
    assert rec.realization == rec.to_pick().realization  # unchanged from insert


def test_persistence_survives_reconnection(tmp_path):
    path = str(tmp_path / "phase8.db")
    conn = Database.open(path)
    Database.migrate(conn)

    SlateStore.insert(conn, SlateRecord(
        slate_id="s1", generated_at="2026-04-20T09:00", sport="MLB", card_type="daily_edge",
    ))
    PickStore.insert(conn, _build_and_pick("MLB", "G1", "BOS"), slate_id="s1")
    conn.close()

    # Reopen
    conn2 = Database.open(path)
    picks = PickStore.list_by_slate(conn2, "s1")
    assert len(picks) == 1
    assert picks[0].game_id == "G1"
    conn2.close()
