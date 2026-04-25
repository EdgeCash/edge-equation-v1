"""NHL API client + ingestor tests.

Mocked HTTP transport throughout -- never hits the real API. Mirror
of test_mlb_stats.py so the two integrations stay uniform.
"""
from datetime import date, datetime
import httpx
import pytest

from edge_equation.persistence.db import Database
from edge_equation.stats.nhle_client import NHLE_BASE, NhleClient
from edge_equation.stats.nhle_ingest import (
    NhleResultsIngestor,
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
    game_id=2024020123,
    home_name="Boston Bruins",
    away_name="Toronto Maple Leafs",
    home_score=4,
    away_score=3,
    state="OFF",
    start_time="2026-04-22T23:30:00Z",
):
    return {
        "id": game_id,
        "startTimeUTC": start_time,
        "gameDate": "2026-04-22",
        "gameState": state,
        "awayTeam": {
            "id": 10,
            "name": {"default": away_name, "fr": away_name},
            "abbrev": "TOR",
            "score": away_score,
        },
        "homeTeam": {
            "id": 6,
            "name": {"default": home_name},
            "abbrev": "BOS",
            "score": home_score,
        },
    }


def _score_payload(games):
    """Wrap a list of game dicts in NHL API's response shape."""
    return {"currentDate": "2026-04-22", "games": games}


# ---------------------------------------------- NhleClient


def test_client_score_for_date_success(conn):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_score_payload([
            _final_game(game_id=1),
            _final_game(game_id=2, home_name="Rangers", away_name="Islanders"),
        ]))

    client = NhleClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        games = client.score_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    assert len(games) == 2
    assert "/score/" in captured["url"]
    assert "2026-04-22" in captured["url"]


def test_client_caches_score_response(conn):
    hits = {"count": 0}

    def handler(request):
        hits["count"] += 1
        return httpx.Response(200, json=_score_payload([_final_game()]))

    client = NhleClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        client.score_for_date(conn, day=date(2026, 4, 22), now=NOW)
        client.score_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    assert hits["count"] == 1, "second call should hit OddsCache, not network"


def test_client_returns_empty_on_http_error(conn):
    def handler(request):
        return httpx.Response(500)

    client = NhleClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        games = client.score_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    assert games == []


def test_client_uses_distinct_cache_prefix(conn):
    """OddsCache entries for NHL must not collide with MLB Stats or
    TheSportsDB entries -- check the prefix is nhle: not mlb_stats:
    or thesportsdb:."""
    def handler(request):
        return httpx.Response(200, json=_score_payload([_final_game()]))

    client = NhleClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        client.score_for_date(conn, day=date(2026, 4, 22), now=NOW)
    finally:
        client.close()
    rows = conn.execute("SELECT cache_key FROM odds_cache").fetchall()
    keys = [r["cache_key"] for r in rows]
    assert any(k.startswith("nhle:") for k in keys)
    assert not any(k.startswith("thesportsdb:") or k.startswith("mlb_stats:") for k in keys)


# ---------------------------------------------- parse_game_as_result


def test_parse_final_game_produces_result():
    result = parse_game_as_result(_final_game())
    assert result is not None
    assert result.game_id == "NHL-STATS-2024020123"
    assert result.league == "NHL"
    assert result.home_team == "Boston Bruins"
    assert result.away_team == "Toronto Maple Leafs"
    assert result.home_score == 4
    assert result.away_score == 3
    assert result.status == "final"


def test_parse_accepts_final_state_alias():
    """Some games come through with gameState='FINAL' rather than 'OFF'."""
    final_alias = _final_game(state="FINAL")
    assert parse_game_as_result(final_alias) is not None


def test_parse_live_game_returns_none():
    live = _final_game(state="LIVE")
    assert parse_game_as_result(live) is None


def test_parse_pre_game_returns_none():
    pre = _final_game(state="PRE")
    assert parse_game_as_result(pre) is None


def test_parse_future_game_returns_none():
    fut = _final_game(state="FUT")
    assert parse_game_as_result(fut) is None


def test_parse_critical_state_returns_none():
    """CRIT means late game / OT -- not yet settled."""
    crit = _final_game(state="CRIT")
    assert parse_game_as_result(crit) is None


def test_parse_missing_scores_returns_none():
    g = _final_game()
    g["homeTeam"]["score"] = None
    assert parse_game_as_result(g) is None


def test_parse_missing_team_name_uses_abbrev():
    """Defensive fallback: if name.default is absent, use abbrev."""
    g = _final_game()
    g["homeTeam"]["name"] = {}
    result = parse_game_as_result(g)
    assert result is not None
    assert result.home_team == "BOS"  # abbrev fallback


def test_parse_missing_team_name_and_abbrev_returns_none():
    g = _final_game()
    g["homeTeam"]["name"] = {}
    g["homeTeam"]["abbrev"] = ""
    assert parse_game_as_result(g) is None


def test_parse_missing_game_id_returns_none():
    g = _final_game()
    del g["id"]
    assert parse_game_as_result(g) is None


# ---------------------------------------------- NhleResultsIngestor


def test_ingestor_writes_finalized_games_to_store(conn):
    def handler(request):
        return httpx.Response(200, json=_score_payload([
            _final_game(game_id=10, home_name="Rangers", away_name="Islanders"),
            _final_game(game_id=11, home_name="Devils", away_name="Flyers"),
        ]))

    client = NhleClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        summary = NhleResultsIngestor.ingest_day(
            conn, day=date(2026, 4, 22), client=client,
        )
    finally:
        client.close()
    assert summary.events_seen == 2
    assert summary.events_finished == 2
    assert summary.results_written == 2
    assert summary.skipped_no_scores == 0
    assert summary.skipped_non_final == 0
    assert GameResultsStore.count_by_league(conn, "NHL") == 2


def test_ingestor_skips_live_and_counts_them(conn):
    def handler(request):
        return httpx.Response(200, json=_score_payload([
            _final_game(game_id=20),  # final, written
            _final_game(game_id=21, state="LIVE"),
            _final_game(game_id=22, state="CRIT"),
        ]))

    client = NhleClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        summary = NhleResultsIngestor.ingest_day(
            conn, day=date(2026, 4, 22), client=client,
        )
    finally:
        client.close()
    assert summary.events_seen == 3
    assert summary.results_written == 1
    # LIVE and CRIT games both have scores populated in our fixture
    # (they're in progress), so they're "non-final" rather than
    # "no scores".
    assert summary.skipped_non_final == 2


def test_ingestor_is_idempotent_on_duplicate_runs(conn):
    """Re-ingesting the same day upserts via UNIQUE(game_id)."""
    def handler(request):
        return httpx.Response(200, json=_score_payload([_final_game(game_id=30)]))

    for _ in range(3):
        client = NhleClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        try:
            NhleResultsIngestor.ingest_day(
                conn, day=date(2026, 4, 22), client=client,
            )
        finally:
            client.close()
    assert GameResultsStore.count_by_league(conn, "NHL") == 1


def test_ingestor_backfill_walks_n_days_back(conn):
    seen_dates = set()

    def handler(request):
        seen_dates.add(str(request.url))
        return httpx.Response(200, json=_score_payload([]))

    client = NhleClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        summary = NhleResultsIngestor.backfill(
            conn, days=3, end_day=date(2026, 4, 22), client=client,
        )
    finally:
        client.close()
    assert summary.days_scanned == 3
    assert summary.leagues_scanned == 1
    assert len(seen_dates) == 3


def test_ingestor_backfill_respects_zero_days(conn):
    summary = NhleResultsIngestor.backfill(conn, days=0)
    assert summary.days_scanned == 0
    assert summary.events_seen == 0
