"""
Phase 27 -- results settler + auto-ingest from TheSportsDB.

Flow under test:

  TheSportsDB events_by_date -> parse_event_as_result -> GameResult
                                                            |
  GameResultsStore <- TheSportsDBResultsIngestor.ingest_day -|
                                                            |
  settle_picks_from_game_results() <- RealizationTracker  <-|

And the CLI wrappers (auto-settle / backfill-results) + the workflow
YAML schedule invariants.
"""
from datetime import date, timedelta
from pathlib import Path
from typing import List

import httpx
import pytest

from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.engine.realization import (
    RealizationTracker,
    SETTLED_LOSS,
    SETTLED_PUSH,
    SETTLED_WIN,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.stats.results import GameResult, GameResultsStore
from edge_equation.stats.thesportsdb_ingest import (
    TheSportsDBResultsIngestor,
    parse_event_as_result,
)


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


# ------------------------------------------------ parse_event_as_result


def _finished_event(home="NYY", away="BOS", hs=5, as_=3):
    return {
        "idEvent": f"EVT-{home}-{away}",
        "strHomeTeam": home,
        "strAwayTeam": away,
        "intHomeScore": str(hs),
        "intAwayScore": str(as_),
        "strStatus": "Match Finished",
        "dateEvent": "2026-04-21",
        "strTime": "19:30:00",
    }


def test_parse_event_accepts_finished_event():
    ev = _finished_event()
    result = parse_event_as_result(ev, "MLB")
    assert result is not None
    assert result.league == "MLB"
    assert result.home_team == "NYY"
    assert result.away_team == "BOS"
    assert result.home_score == 5
    assert result.away_score == 3
    assert result.game_id == "EVT-NYY-BOS"
    assert result.status == "final"


def test_parse_event_drops_event_missing_scores():
    ev = _finished_event()
    ev["intHomeScore"] = None
    assert parse_event_as_result(ev, "MLB") is None


def test_parse_event_drops_future_game_without_final_status():
    future = date.today() + timedelta(days=1)
    ev = _finished_event()
    ev["strStatus"] = ""
    ev["dateEvent"] = future.isoformat()
    assert parse_event_as_result(ev, "MLB") is None


def test_parse_event_accepts_past_game_even_if_status_blank():
    past = date.today() - timedelta(days=2)
    ev = _finished_event()
    ev["strStatus"] = ""
    ev["dateEvent"] = past.isoformat()
    result = parse_event_as_result(ev, "MLB")
    assert result is not None


def test_parse_event_returns_none_on_missing_teams():
    ev = _finished_event()
    ev["strHomeTeam"] = ""
    assert parse_event_as_result(ev, "MLB") is None


def test_parse_event_returns_none_on_garbage_scores():
    ev = _finished_event()
    ev["intHomeScore"] = "not-a-number"
    assert parse_event_as_result(ev, "MLB") is None


# ------------------------------------------------ TheSportsDBResultsIngestor


class _StubSportsDBClient:
    """Minimal stand-in that returns a fixed event list per league/day,
    tracks call counts, and provides close() so the ingestor's "owns
    client" path works."""

    def __init__(self, events_by_league):
        self._events = events_by_league
        self.calls: List[tuple] = []

    def events_by_date(self, conn, day, league_id, now=None, cached_only=False):
        league = next(
            (lg for lg, lid in _LEAGUE_TO_ID.items() if lid == league_id),
            "UNKNOWN",
        )
        self.calls.append((day.isoformat(), league))
        return list(self._events.get(league, []))

    def close(self):
        pass


# Keep in sync with data_fetcher.THESPORTSDB_LEAGUE_IDS; only the
# subset we exercise in these tests.
_LEAGUE_TO_ID = {"MLB": 4424, "NBA": 4387, "NFL": 4391, "NHL": 4380}


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def test_ingest_day_writes_finished_games_to_store(conn):
    client = _StubSportsDBClient({"MLB": [_finished_event()]})
    summary = TheSportsDBResultsIngestor.ingest_day(
        conn, day=date(2026, 4, 21),
        leagues=["MLB"], client=client,
    )
    assert summary.results_written == 1
    assert summary.events_seen == 1
    assert summary.events_finished == 1
    stored = GameResultsStore.count_by_league(conn, "MLB")
    assert stored == 1


def test_ingest_day_skips_non_final_counts_separately(conn):
    future_ev = _finished_event()
    future = date.today() + timedelta(days=1)
    future_ev["dateEvent"] = future.isoformat()
    future_ev["strStatus"] = "Scheduled"
    client = _StubSportsDBClient({"MLB": [future_ev]})
    summary = TheSportsDBResultsIngestor.ingest_day(
        conn, day=date(2026, 4, 21),
        leagues=["MLB"], client=client,
    )
    assert summary.results_written == 0
    assert summary.events_seen == 1


def test_ingest_day_is_idempotent(conn):
    client = _StubSportsDBClient({"MLB": [_finished_event()]})
    s1 = TheSportsDBResultsIngestor.ingest_day(
        conn, day=date(2026, 4, 21),
        leagues=["MLB"], client=client,
    )
    s2 = TheSportsDBResultsIngestor.ingest_day(
        conn, day=date(2026, 4, 21),
        leagues=["MLB"], client=client,
    )
    # Both runs report a write, but GameResultsStore.record upserts,
    # so total row count stays at 1.
    assert s1.results_written == 1
    assert s2.results_written == 1
    assert GameResultsStore.count_by_league(conn, "MLB") == 1


def test_backfill_scans_multiple_days(conn):
    day = date.today() - timedelta(days=1)
    events = [{**_finished_event(), "dateEvent": (day - timedelta(days=i)).isoformat(),
               "idEvent": f"EVT-{i}"} for i in range(3)]
    # Return different events per day so we see distinct writes.
    by_day_events = {
        (day - timedelta(days=i)).isoformat(): [events[i]] for i in range(3)
    }

    class _PerDayClient:
        def __init__(self): self.calls = []
        def events_by_date(self, conn, day, league_id, now=None, cached_only=False):
            self.calls.append(day.isoformat())
            return by_day_events.get(day.isoformat(), [])
        def close(self): pass

    client = _PerDayClient()
    summary = TheSportsDBResultsIngestor.backfill(
        conn, days=3, end_day=day,
        leagues=["MLB"], client=client,
    )
    assert summary.days_scanned == 3
    assert summary.results_written == 3
    assert GameResultsStore.count_by_league(conn, "MLB") == 3


# ------------------------------------------------ settle_pick_vs_result


def test_ml_settles_home_win():
    r = RealizationTracker.settle_pick_vs_result(
        "ML", "NYY", "NYY", "BOS", home_score=5, away_score=3,
    )
    assert r == SETTLED_WIN


def test_ml_settles_loss_on_wrong_selection():
    r = RealizationTracker.settle_pick_vs_result(
        "ML", "BOS", "NYY", "BOS", home_score=5, away_score=3,
    )
    assert r == SETTLED_LOSS


def test_ml_settles_push_on_draw():
    r = RealizationTracker.settle_pick_vs_result(
        "ML", "NYY", "NYY", "BOS", home_score=2, away_score=2,
    )
    assert r == SETTLED_PUSH


def test_totals_over_wins_under_loses():
    r_over = RealizationTracker.settle_pick_vs_result(
        "Total", "Over 9", "NYY", "BOS", home_score=6, away_score=5,
    )
    r_under = RealizationTracker.settle_pick_vs_result(
        "Total", "Under 9", "NYY", "BOS", home_score=6, away_score=5,
    )
    assert r_over == SETTLED_WIN
    assert r_under == SETTLED_LOSS


def test_totals_push_when_exact():
    r = RealizationTracker.settle_pick_vs_result(
        "Total", "Over 9", "NYY", "BOS", home_score=5, away_score=4,
    )
    assert r == SETTLED_PUSH


def test_spread_home_favorite_covers():
    # NYY -1.5: home needs to win by 2+.
    r = RealizationTracker.settle_pick_vs_result(
        "Spread", "NYY -1.5", "NYY", "BOS", home_score=5, away_score=3,
    )
    assert r == SETTLED_WIN


def test_spread_home_favorite_fails_to_cover():
    # NYY -1.5, wins by exactly 1 -> loss.
    r = RealizationTracker.settle_pick_vs_result(
        "Spread", "NYY -1.5", "NYY", "BOS", home_score=4, away_score=3,
    )
    assert r == SETTLED_LOSS


def test_spread_away_dog_covers():
    # BOS +2.5: covers unless home wins by 3+.
    r = RealizationTracker.settle_pick_vs_result(
        "Spread", "BOS +2.5", "NYY", "BOS", home_score=4, away_score=3,
    )
    assert r == SETTLED_WIN


def test_props_return_none():
    """Prop markets aren't auto-settleable from a game result alone."""
    r = RealizationTracker.settle_pick_vs_result(
        "HR", "Aaron Judge over 0.5", "NYY", "BOS",
        home_score=5, away_score=3,
    )
    assert r is None


def test_unknown_selection_returns_none():
    r = RealizationTracker.settle_pick_vs_result(
        "ML", "NON-TEAM", "NYY", "BOS", home_score=5, away_score=3,
    )
    assert r is None


# ------------------------------------------------ end-to-end settle_picks_from_game_results


def _seed_slate_with_picks(conn, picks: List[Pick]) -> str:
    slate_id = "test-slate"
    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id, generated_at="2026-04-21T11:00:00",
        sport="MLB", card_type="daily_edge", metadata={},
    ))
    PickStore.insert_many(conn, picks, slate_id=slate_id,
                          recorded_at="2026-04-21T11:00:00")
    return slate_id


def test_auto_settle_matches_picks_to_game_results(conn):
    # Seed a game result and two picks on that game.
    GameResultsStore.record(conn, GameResult(
        result_id=None, game_id="G-NYY-BOS",
        league="MLB", home_team="NYY", away_team="BOS",
        start_time="2026-04-21T19:30:00",
        home_score=7, away_score=3, status="final",
    ))
    picks = [
        Pick(sport="MLB", market_type="ML", selection="NYY",
             line=Line(odds=-125), game_id="G-NYY-BOS"),
        Pick(sport="MLB", market_type="Total", selection="Over 9",
             line=Line(odds=-110), game_id="G-NYY-BOS"),
    ]
    _seed_slate_with_picks(conn, picks)
    # Force pending state.
    conn.execute("UPDATE picks SET realization = 47")
    conn.commit()

    summary = RealizationTracker.settle_picks_from_game_results(conn)
    assert summary["updated"] == 2
    assert summary["unmatchable"] == 0

    # Verify the picks' realization column moved off pending.
    rows = conn.execute("SELECT market_type, realization FROM picks").fetchall()
    by_market = {r["market_type"]: int(r["realization"]) for r in rows}
    assert by_market["ML"] == SETTLED_WIN
    assert by_market["Total"] == SETTLED_WIN   # 10 > 9


def test_auto_settle_leaves_pending_picks_without_result_alone(conn):
    picks = [
        Pick(sport="MLB", market_type="ML", selection="NYY",
             line=Line(odds=-125), game_id="G-NOT-YET-FINAL"),
    ]
    _seed_slate_with_picks(conn, picks)
    conn.execute("UPDATE picks SET realization = 47")
    conn.commit()

    # No game_results row for this game_id.
    summary = RealizationTracker.settle_picks_from_game_results(conn)
    assert summary["updated"] == 0
    # Pick still pending.
    row = conn.execute(
        "SELECT realization FROM picks WHERE game_id = 'G-NOT-YET-FINAL'"
    ).fetchone()
    assert int(row["realization"]) == 47


def test_auto_settle_is_idempotent(conn):
    GameResultsStore.record(conn, GameResult(
        result_id=None, game_id="G-IDEM",
        league="MLB", home_team="NYY", away_team="BOS",
        start_time="2026-04-21T19:30:00",
        home_score=5, away_score=3, status="final",
    ))
    _seed_slate_with_picks(conn, [
        Pick(sport="MLB", market_type="ML", selection="NYY",
             line=Line(odds=-125), game_id="G-IDEM"),
    ])
    conn.execute("UPDATE picks SET realization = 47")
    conn.commit()

    s1 = RealizationTracker.settle_picks_from_game_results(conn)
    s2 = RealizationTracker.settle_picks_from_game_results(conn)
    # First run settles the 1 pending pick. Second run finds zero
    # pending picks on that join (they've moved to WIN).
    assert s1["updated"] == 1
    assert s2["matched"] == 0
    assert s2["updated"] == 0


# ------------------------------------------------ workflow schedule


def test_results_settler_workflow_has_2am_ct_dual_cron():
    text = (WORKFLOWS / "results-settler.yml").read_text(encoding="utf-8")
    # 02:00 CT -> UTC 07 (CDT), UTC 08 (CST).
    assert 'cron: "0 7 * * *"' in text
    assert 'cron: "0 8 * * *"' in text
    # CT-hour guard pins hour == 2.
    assert "now.hour == 2" in text


def test_results_settler_workflow_invokes_auto_settle():
    text = (WORKFLOWS / "results-settler.yml").read_text(encoding="utf-8")
    assert "python -m edge_equation auto-settle" in text


def test_results_settler_uses_phase26c_cache_pattern():
    """Settler is now a second writer to the shared cache; it must use
    the same read-only restore + unique-key save pattern as the
    refresher so it doesn't stomp the primary key."""
    text = (WORKFLOWS / "results-settler.yml").read_text(encoding="utf-8")
    assert "actions/cache/restore@v4" in text
    assert "actions/cache/save@v4" in text
    # Save key must include run_id for uniqueness.
    assert "edge-equation-db-${{ github.ref_name }}-${{ github.run_id }}" in text
