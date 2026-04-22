"""
Credit guardrail end-to-end: cached_only=True from CLI -> ScheduledRunner
-> SourceFactory -> TheOddsApiSource -> TheOddsApiClient. No live Odds
API call on any path.

Also locks:
  - TheOddsApiSource default ttl_seconds is 6h (matches refresher).
  - Every cadence workflow passes --cached-only.
  - data-refresher.yml runs at 07:00 + 15:00 CT (shifted from 08:00).
"""
import re
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from edge_equation.__main__ import build_parser
from edge_equation.engine.scheduled_runner import ScheduledRunner
from edge_equation.ingestion.odds_api_client import TheOddsApiClient
from edge_equation.ingestion.odds_api_source import TheOddsApiSource
from edge_equation.ingestion.source_factory import SourceFactory
from edge_equation.persistence.db import Database


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _exploding_client():
    def handler(request):
        raise AssertionError(
            f"cached_only=True made a network call: {request.method} {request.url}"
        )
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------- TheOddsApiSource direct


def test_odds_api_source_default_ttl_is_six_hours():
    src = TheOddsApiSource(conn=None, sport_key="baseball_mlb")
    assert src.ttl_seconds == 6 * 60 * 60


def test_odds_api_source_cached_only_kwarg_default_off():
    src = TheOddsApiSource(conn=None, sport_key="baseball_mlb")
    assert src.cached_only is False


def test_odds_api_source_cached_only_returns_empty_on_miss(conn, monkeypatch):
    """A cache miss + cached_only=True must not make a network call
    and must yield an empty slate."""
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    src = TheOddsApiSource(
        conn=conn, sport_key="baseball_mlb", cached_only=True,
    )
    exploder = _exploding_client()
    games = src.get_raw_games(run_datetime=datetime.utcnow(), http_client=exploder)
    markets = src.get_raw_markets(run_datetime=datetime.utcnow(), http_client=exploder)
    assert games == []
    assert markets == []


def test_odds_api_source_cached_only_serves_prior_cache_entry(conn):
    """If the refresher wrote a payload earlier, cached_only reads it."""
    # Prime the cache via a normal call (handler returns a valid payload).
    def handler(request):
        return httpx.Response(200, json=[{
            "id": "g-1",
            "commence_time": "2026-04-22T18:00:00Z",
            "home_team": "NYY",
            "away_team": "BOS",
            "bookmakers": [],
        }])
    client = httpx.Client(transport=httpx.MockTransport(handler))
    # Prime the cache using the SAME markets list TheOddsApiSource
    # (and the Data Refresher) use by default. If these drift apart,
    # cache keys mismatch and cached_only reads return empty -- the
    # Phase 26d bug.
    TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb",
        markets=["h2h", "totals", "spreads"], regions="us",
        http_client=client, api_key="k",
    )
    # Now a cached_only source reads the prior write without touching
    # the network.
    src = TheOddsApiSource(
        conn=conn, sport_key="baseball_mlb", cached_only=True,
    )
    exploder = _exploding_client()
    games = src.get_raw_games(run_datetime=datetime.utcnow(), http_client=exploder)
    assert len(games) == 1
    assert games[0]["home_team"] == "NYY"


# --------------------------------------------- SourceFactory threads the flag


def test_source_factory_threads_cached_only(monkeypatch):
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    conn = object()  # SourceFactory never uses it beyond "not None"
    src = SourceFactory.for_league(
        league="MLB", run_date=date(2026, 4, 22),
        conn=conn, cached_only=True,
    )
    assert isinstance(src, TheOddsApiSource)
    assert src.cached_only is True


def test_source_factory_default_is_not_cached_only(monkeypatch):
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    src = SourceFactory.for_league(
        league="MLB", run_date=date(2026, 4, 22),
        conn=object(),
    )
    assert src.cached_only is False


# --------------------------------------------- CLI flag


def test_cli_cached_only_flag_default_off():
    args = build_parser().parse_args(["daily"])
    assert args.cached_only is False


def test_cli_cached_only_flag_parses():
    args = build_parser().parse_args(["daily", "--cached-only"])
    assert args.cached_only is True


# --------------------------------------------- ScheduledRunner.run signature


def test_runner_accepts_cached_only(conn):
    # Smoke only: confirms the kwarg is accepted end-to-end. With
    # prefer_mock=True we never touch the network regardless.
    summary = ScheduledRunner.run(
        card_type="daily_edge", conn=conn,
        run_datetime=datetime(2026, 4, 22, 11, 0),
        leagues=["MLB"], prefer_mock=True, cached_only=True,
    )
    assert summary.slate_id.startswith("daily_edge_")


# --------------------------------------------- workflow YAML invariants

_CADENCE_WORKFLOWS = (
    ("ledger.yml", "ledger"),
    ("daily-edge.yml", "daily"),
    ("spotlight.yml", "spotlight"),
    ("evening-edge.yml", "evening"),
    ("overseas-edge.yml", "overseas"),
)


@pytest.mark.parametrize("filename,subcommand", _CADENCE_WORKFLOWS)
def test_cadence_workflow_passes_cached_only(filename, subcommand):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert "--cached-only" in text, (
        f"{filename} must pass --cached-only so the free-tier credit "
        f"budget isn't blown on every cadence run"
    )


def test_refresher_workflow_runs_at_7am_and_3pm_ct():
    text = (WORKFLOWS / "data-refresher.yml").read_text(encoding="utf-8")
    # 07:00 CT -> UTC 12 (CDT) / UTC 13 (CST).
    assert 'cron: "0 12 * * *"' in text
    assert 'cron: "0 13 * * *"' in text
    # 15:00 CT -> UTC 20 (CDT) / UTC 21 (CST).
    assert 'cron: "0 20 * * *"' in text
    assert 'cron: "0 21 * * *"' in text
    # CT-hour guard picks exactly one slot per day.
    assert "now.hour in (7, 15)" in text


def test_refresher_workflow_never_passes_cached_only():
    """The refresher is the SOLE live-API consumer. --cached-only on the
    refresher would defeat the entire purpose (it'd skip the live call)."""
    text = (WORKFLOWS / "data-refresher.yml").read_text(encoding="utf-8")
    assert "--cached-only" not in text
    assert "refresh-data" in text
