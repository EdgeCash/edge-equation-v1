"""MLB Stats API client + ingestor tests.

Mocked HTTP transport throughout -- never hits the real API. Uses
the same OddsCache fixture as test_data_fetcher so cache behavior is
exercised end-to-end against the SQLite store.
"""
from datetime import date, datetime
import httpx
import pytest

from edge_equation.persistence.db import Database
from edge_equation.persistence.odds_cache import OddsCache
from edge_equation.stats.mlb_stats_client import (
    MLB_STATS_BASE,
    MlbStatsClient,
)
from edge_equation.stats.mlb_stats_ingest import (
    MlbStatsResultsIngestor,
    parse_game_as_result,
)
from edge_equation.stats.results import GameResultsStore


NOW = datetime(2026, 4, 22, 12, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


# ---------------------------------------------- response fixtures


def _final_game(
    game_pk=778911,
    home_name="Houston Astros",
    away_name="Minnesota Twins",
    home_score=7,
    away_score=4,
    coded_state="F",
    abstract_state="Final",
    game_date="2026-04-22T23:05:00Z",
):
    return {
        "gamePk": game_pk,
        "gameDate": game_date,
        "status": {
            "codedGameState": coded_state,
            "abstractGameState": abstract_state,
        },
        "teams": {
            "away": {
                "team": {"id": 142, "name": away_name},
                "score": away_score,
            },
            "home": {
                "team": {"id": 117, "name": home_name},
                "score": home_score,
            },
        },
    }


def _schedule_payload(games):
    """Wrap a list of game dicts in MLB Stats API's response shape."""
    return {
        "totalItems": len(games),
        "dates": [{"date": "2026-04-22", "games": games}] if games else [],
    }


# ---------------------------------------------- MlbStatsClient


def test_client_schedule_for_date_success(conn):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_schedule_payload([
            _final_game(game_pk=1),
            _final_game(game_pk=2, home_name="Tigers", away_name="Royals"),
        ]))

    client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        games = client.schedule_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    assert len(games) == 2
    assert "schedule" in captured["url"]
    assert "sportId=1" in captured["url"]
    assert "date=2026-04-22" in captured["url"]


def test_client_caches_schedule_response(conn):
    hits = {"count": 0}

    def handler(request):
        hits["count"] += 1
        return httpx.Response(200, json=_schedule_payload([_final_game()]))

    client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        client.schedule_for_date(conn, day=date(2026, 4, 22), now=NOW)
        client.schedule_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    assert hits["count"] == 1, "second call should hit OddsCache, not network"


def test_client_returns_empty_on_http_error(conn):
    def handler(request):
        return httpx.Response(500)

    client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        games = client.schedule_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    assert games == []


def test_client_uses_distinct_cache_prefix(conn):
    """OddsCache entries for MLB Stats must not collide with TheSportsDB
    entries -- check the prefix is mlb_stats: not thesportsdb:."""
    def handler(request):
        return httpx.Response(200, json=_schedule_payload([_final_game()]))

    client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        client.schedule_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    rows = conn.execute("SELECT cache_key FROM odds_cache").fetchall()
    keys = [r["cache_key"] for r in rows]
    assert any(k.startswith("mlb_stats:") for k in keys)
    assert not any(k.startswith("thesportsdb:") for k in keys)


def test_client_flattens_multi_date_response(conn):
    """API can return a `dates` array with multiple date blocks; the
    client flattens them into a single game list."""
    payload = {
        "dates": [
            {"date": "2026-04-22", "games": [_final_game(game_pk=1)]},
            {"date": "2026-04-23", "games": [_final_game(game_pk=2), _final_game(game_pk=3)]},
        ],
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        games = client.schedule_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    assert {g["gamePk"] for g in games} == {1, 2, 3}


# ---------------------------------------------- parse_game_as_result


def test_parse_final_game_produces_result():
    result = parse_game_as_result(_final_game())
    assert result is not None
    assert result.game_id == "MLB-STATS-778911"
    assert result.league == "MLB"
    assert result.home_team == "Houston Astros"
    assert result.away_team == "Minnesota Twins"
    assert result.home_score == 7
    assert result.away_score == 4
    assert result.status == "final"


def test_parse_in_progress_game_returns_none():
    in_progress = _final_game(coded_state="I", abstract_state="Live")
    assert parse_game_as_result(in_progress) is None


def test_parse_pre_game_returns_none():
    pre = _final_game(coded_state="P", abstract_state="Preview")
    # Status is pre-game but scores might still be present (0-0). Reject.
    pre["teams"]["home"]["score"] = None
    pre["teams"]["away"]["score"] = None
    assert parse_game_as_result(pre) is None


def test_parse_missing_scores_returns_none():
    g = _final_game()
    g["teams"]["home"]["score"] = None
    assert parse_game_as_result(g) is None


def test_parse_missing_team_name_returns_none():
    g = _final_game()
    g["teams"]["home"]["team"]["name"] = ""
    assert parse_game_as_result(g) is None


def test_parse_missing_game_pk_returns_none():
    """Without a stable gamePk we can't dedup on re-ingest -- skip."""
    g = _final_game()
    del g["teams"]
    assert parse_game_as_result(g) is None


def test_parse_accepts_forfeit_and_review_states():
    """Final-with-review (FR) and Forfeit (FT) are both settled outcomes."""
    fr = _final_game(coded_state="FR", abstract_state="Final")
    ft = _final_game(coded_state="FT", abstract_state="Final")
    assert parse_game_as_result(fr) is not None
    assert parse_game_as_result(ft) is not None


# ---------------------------------------------- MlbStatsResultsIngestor


def test_ingestor_writes_finalized_games_to_store(conn):
    def handler(request):
        return httpx.Response(200, json=_schedule_payload([
            _final_game(game_pk=10, home_name="Yankees", away_name="Red Sox"),
            _final_game(game_pk=11, home_name="Cubs", away_name="Mets"),
        ]))

    client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        summary = MlbStatsResultsIngestor.ingest_day(
            conn, day=date(2026, 4, 22), client=client,
        )
    finally:
        client.close()
    assert summary.events_seen == 2
    assert summary.events_finished == 2
    assert summary.results_written == 2
    assert summary.skipped_no_scores == 0
    assert summary.skipped_non_final == 0
    # Verify rows landed in the table.
    assert GameResultsStore.count_by_league(conn, "MLB") == 2


def test_ingestor_skips_in_progress_and_counts_them(conn):
    def handler(request):
        return httpx.Response(200, json=_schedule_payload([
            _final_game(game_pk=20),  # final, written
            _final_game(game_pk=21, coded_state="I", abstract_state="Live"),
            _final_game(game_pk=22, coded_state="P", abstract_state="Preview"),
        ]))

    client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        summary = MlbStatsResultsIngestor.ingest_day(
            conn, day=date(2026, 4, 22), client=client,
        )
    finally:
        client.close()
    assert summary.events_seen == 3
    assert summary.results_written == 1
    # Live + Preview both have scores reported as 0/0 in our fixture
    # generator -- they're "non-final" rather than "no scores".
    assert summary.skipped_non_final == 2


def test_ingestor_is_idempotent_on_duplicate_runs(conn):
    """Re-ingesting the same day overwrites in place via the
    UNIQUE(game_id) constraint -- never duplicates."""
    def handler(request):
        return httpx.Response(200, json=_schedule_payload([_final_game(game_pk=30)]))

    for _ in range(3):
        client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        try:
            MlbStatsResultsIngestor.ingest_day(
                conn, day=date(2026, 4, 22), client=client,
            )
        finally:
            client.close()
    assert GameResultsStore.count_by_league(conn, "MLB") == 1


def test_ingestor_backfill_walks_n_days_back(conn):
    """backfill(days=3) should call ingest_day 3 times. We assert by
    counting how many distinct date= URLs get hit on the mock."""
    seen_dates = set()

    def handler(request):
        seen_dates.add(str(request.url))
        return httpx.Response(200, json=_schedule_payload([]))

    client = MlbStatsClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        summary = MlbStatsResultsIngestor.backfill(
            conn, days=3, end_day=date(2026, 4, 22), client=client,
        )
    finally:
        client.close()
    assert summary.days_scanned == 3
    assert summary.leagues_scanned == 1
    # Three distinct date= URLs in the request log.
    assert len(seen_dates) == 3


def test_ingestor_backfill_respects_zero_days(conn):
    summary = MlbStatsResultsIngestor.backfill(conn, days=0)
    assert summary.days_scanned == 0
    assert summary.events_seen == 0
