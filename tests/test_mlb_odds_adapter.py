"""
Contract tests for the MLB odds adapter that bridges v1's odds-fetching
surface to scrapers' clv_tracker pricing path.

The adapter MUST translate every raw Odds API response shape into a
nested per-game dict that clv_tracker.find_closing_price() can resolve
for moneyline, run-line, and totals picks. These tests pin that
contract — if the upstream Odds API response shape ever drifts or
clv_tracker's spec format changes, one of these will fail loud.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from edge_equation.exporters.mlb import clv_tracker
from edge_equation.exporters.mlb._odds_adapter import MLBOddsScraper


def _mock_client(payload, headers=None):
    """httpx.MockTransport returning `payload` for any GET."""
    def handler(request):
        return httpx.Response(200, json=payload, headers=headers or {})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def chc_az_game():
    return [{
        "id": "abc123",
        "commence_time": "2026-05-04T22:05:00Z",
        "home_team": "CHC", "away_team": "AZ",
        "bookmakers": [{
            "key": "draftkings",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "CHC", "price": -118},
                    {"name": "AZ",  "price": +102},
                ]},
                {"key": "spreads", "outcomes": [
                    {"name": "CHC", "point": -1.5, "price": +145},
                    {"name": "AZ",  "point": +1.5, "price": -165},
                ]},
                {"key": "totals", "outcomes": [
                    {"name": "Over",  "point": 8.5, "price": -110},
                    {"name": "Under", "point": 8.5, "price": -110},
                ]},
            ],
        }],
    }]


def test_translates_to_clv_tracker_shape(chc_az_game):
    client = _mock_client(chc_az_game)
    result = MLBOddsScraper(api_key="TEST", http_client=client).fetch()
    assert result["source"] == "the-odds-api"
    g = result["games"][0]
    assert g["home_team"] == "CHC" and g["away_team"] == "AZ"
    assert g["moneyline"]["home"]["american"] == -118
    assert g["moneyline"]["away"]["american"] == +102
    assert g["totals"][0]["point"] == 8.5
    assert g["totals"][0]["over"]["american"] == -110
    assert g["totals"][0]["under"]["american"] == -110


@pytest.mark.parametrize("spec, expected_american", [
    ({"type": "moneyline", "team": "CHC"}, -118),
    ({"type": "moneyline", "team": "AZ"}, +102),
    ({"type": "run_line", "team": "CHC", "point": -1.5}, +145),
    ({"type": "run_line", "team": "AZ", "point": +1.5}, -165),
    ({"type": "totals", "side": "OVER", "line": 8.5}, -110),
    ({"type": "totals", "side": "UNDER", "line": 8.5}, -110),
])
def test_clv_tracker_can_price_every_supported_spec(chc_az_game, spec, expected_american):
    client = _mock_client(chc_az_game)
    g = MLBOddsScraper(api_key="TEST", http_client=client).fetch()["games"][0]
    priced = clv_tracker.find_closing_price(g, spec)
    assert priced is not None, f"adapter omitted price for {spec}"
    assert priced["american"] == expected_american, priced


def test_returns_none_for_unpriced_total_line(chc_az_game):
    client = _mock_client(chc_az_game)
    g = MLBOddsScraper(api_key="TEST", http_client=client).fetch()["games"][0]
    assert clv_tracker.find_closing_price(
        g, {"type": "totals", "side": "OVER", "line": 7.5},
    ) is None


def test_handles_embedded_point_in_total_outcome_name():
    payload = [{
        "id": "xyz", "commence_time": "2026-05-04T23:00:00Z",
        "home_team": "NYY", "away_team": "BOS",
        "bookmakers": [{"key": "fanduel", "markets": [
            {"key": "totals", "outcomes": [
                {"name": "Over 9.0", "point": None, "price": -105},
                {"name": "Under 9.0", "point": None, "price": -115},
            ]},
        ]}],
    }]
    client = _mock_client(payload)
    g = MLBOddsScraper(api_key="TEST", http_client=client).fetch()["games"][0]
    assert g["totals"][0]["point"] == 9.0
    assert g["totals"][0]["over"]["american"] == -105
    assert g["totals"][0]["under"]["american"] == -115


def test_skips_games_with_no_priced_markets():
    payload = [{
        "id": "empty", "commence_time": "2026-05-04T23:00:00Z",
        "home_team": "X", "away_team": "Y",
        "bookmakers": [{"key": "draftkings", "markets": []}],
    }]
    client = _mock_client(payload)
    result = MLBOddsScraper(api_key="TEST", http_client=client).fetch()
    assert result["games"] == []


def test_quota_log_persists(tmp_path: Path, chc_az_game):
    qpath = tmp_path / "quota_log.json"
    client = _mock_client(
        chc_az_game,
        headers={"x-requests-remaining": "490", "x-requests-used": "10"},
    )
    MLBOddsScraper(api_key="TEST", http_client=client, quota_log_path=qpath).fetch()
    payload = json.loads(qpath.read_text())
    assert payload["records"][-1]["remaining"] == 490
    assert payload["records"][-1]["used"] == 10


def test_quota_log_caps_at_500_records(tmp_path: Path, chc_az_game):
    qpath = tmp_path / "quota_log.json"
    qpath.write_text(json.dumps({
        "records": [{"at": str(i), "remaining": i, "used": i} for i in range(498)],
    }))
    client = _mock_client(
        chc_az_game,
        headers={"x-requests-remaining": "1", "x-requests-used": "499"},
    )
    MLBOddsScraper(api_key="TEST", http_client=client, quota_log_path=qpath).fetch()
    payload = json.loads(qpath.read_text())
    assert len(payload["records"]) == 499  # 498 prior + 1 new
    # Hit the cap on the next call
    client2 = _mock_client(
        chc_az_game,
        headers={"x-requests-remaining": "0", "x-requests-used": "500"},
    )
    MLBOddsScraper(api_key="TEST", http_client=client2, quota_log_path=qpath).fetch()
    MLBOddsScraper(api_key="TEST", http_client=_mock_client(chc_az_game,
        headers={"x-requests-remaining": "0", "x-requests-used": "501"})).fetch()  # no qlog
    payload2 = json.loads(qpath.read_text())
    assert len(payload2["records"]) <= 500


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    client = _mock_client([])
    with pytest.raises(RuntimeError, match="Odds API key not set"):
        MLBOddsScraper(http_client=client).fetch()


def test_env_var_fallback_order(monkeypatch, chc_az_game):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEY", "env-key")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=chc_az_game)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    MLBOddsScraper(http_client=client).fetch()
    assert "apiKey=env-key" in seen["url"]


def test_preferred_bookmaker_selected_when_present():
    payload = [{
        "id": "g1", "commence_time": "2026-05-04T23:00:00Z",
        "home_team": "A", "away_team": "B",
        "bookmakers": [
            {"key": "betmgm", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "A", "price": -100}, {"name": "B", "price": -100},
                ]},
            ]},
            {"key": "fanduel", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "A", "price": -200}, {"name": "B", "price": +180},
                ]},
            ]},
        ],
    }]
    client = _mock_client(payload)
    g = MLBOddsScraper(
        api_key="TEST", http_client=client, preferred_bookmaker="fanduel",
    ).fetch()["games"][0]
    assert g["moneyline"]["home"]["book"] == "fanduel"
    assert g["moneyline"]["home"]["american"] == -200
