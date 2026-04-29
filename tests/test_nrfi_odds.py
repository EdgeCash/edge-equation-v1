"""Tests for the NRFI/YRFI closing-line capture (Phase 4).

The Odds API integration is mocked end-to-end via a fake `httpx.Client`-
shaped object so the suite never touches the network. Tests cover:

* Team-name → tricode mapping (Athletics rename, edge cases)
* Bookmaker preference order
* NRFI/YRFI outcome extraction with the 0.5 line filter
* Event-to-game matching across multiple slate days
* Best-effort error handling (missing API key, API 4xx, no events)
* DDL idempotency
* `lookup_closing_odds` returning None for misses
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import pytest

from edge_equation.engines.nrfi.data import odds as odds_mod


# ---------------------------------------------------------------------------
# Fake httpx client / response
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Records GET calls and returns scripted responses."""
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._responses: list[_FakeResponse] = []
        self._raise: Exception | None = None

    def add_response(self, payload: Any, status: int = 200) -> None:
        self._responses.append(_FakeResponse(payload, status))

    def raise_on_next(self, exc: Exception) -> None:
        self._raise = exc

    def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        self.calls.append((url, dict(params or {})))
        if self._raise is not None:
            exc = self._raise
            self._raise = None
            raise exc
        if not self._responses:
            raise AssertionError(f"unexpected GET {url} — no scripted response")
        return self._responses.pop(0)

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fake NRFIStore — duck-types just enough for the odds module
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, games_df: pd.DataFrame | None = None):
        self.games_df = games_df if games_df is not None else pd.DataFrame()
        self.upserts: list[tuple[str, list[dict]]] = []
        self.executed: list[tuple[str, tuple]] = []
        self._query_responses: list[tuple[str, pd.DataFrame]] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql.strip(), tuple(params or ())))

    def upsert(self, table: str, rows) -> int:
        rows = list(rows)
        self.upserts.append((table, rows))
        return len(rows)

    def games_for_date(self, game_date: str):
        return self.games_df

    def queue_query(self, needle: str, df: pd.DataFrame) -> None:
        self._query_responses.append((needle, df))

    def query_df(self, sql: str, params: tuple = ()):
        normalised = " ".join(sql.split())
        for i, (needle, df) in enumerate(self._query_responses):
            if needle in normalised:
                self._query_responses.pop(i)
                return df
        raise AssertionError(
            f"unexpected query_df: no canned response for SQL\n  {normalised!r}"
        )


# ---------------------------------------------------------------------------
# Team-name mapping
# ---------------------------------------------------------------------------


def test_full_name_to_tricode_covers_all_30_teams():
    """All 30 MLB clubs must resolve. Catches typos in the dict and
    silently-renamed franchises (Cleveland Guardians 2022, Athletics 2025)."""
    expected = {
        "ARI", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE", "COL", "DET",
        "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN", "NYM", "NYY", "OAK",
        "PHI", "PIT", "SD", "SF", "SEA", "STL", "TB", "TEX", "TOR", "WSH",
    }
    seen = set(odds_mod._MLB_FULL_NAME_TO_TRICODE.values())
    assert seen >= expected, f"missing tricodes: {expected - seen}"


def test_full_name_athletics_rename_resolves_to_oak():
    """The 2025 Athletics rename — both the historical and current names
    must map to OAK so back-tests against pre-2025 data don't regress."""
    assert odds_mod._full_name_to_tricode("Oakland Athletics") == "OAK"
    assert odds_mod._full_name_to_tricode("Athletics") == "OAK"


def test_full_name_unknown_returns_none():
    assert odds_mod._full_name_to_tricode("Madrid CF") is None
    assert odds_mod._full_name_to_tricode("") is None


# ---------------------------------------------------------------------------
# Bookmaker selection / outcome extraction
# ---------------------------------------------------------------------------


def test_select_book_prefers_draftkings():
    bookmakers = [
        {"key": "betmgm", "markets": []},
        {"key": "draftkings", "markets": []},
        {"key": "fanduel", "markets": []},
    ]
    assert odds_mod._select_book(bookmakers)["key"] == "draftkings"


def test_select_book_falls_back_to_first_unknown_book():
    bookmakers = [
        {"key": "exotic_book", "markets": []},
        {"key": "another_book", "markets": []},
    ]
    assert odds_mod._select_book(bookmakers)["key"] == "exotic_book"


def test_select_book_returns_none_when_empty():
    assert odds_mod._select_book([]) is None


def test_extract_nrfi_yrfi_outcomes_parses_under_and_over():
    payload = {
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "totals_1st_1_innings",
                        "outcomes": [
                            {"name": "Under", "point": 0.5, "price": -125},
                            {"name": "Over",  "point": 0.5, "price": +105},
                        ],
                    },
                ],
            },
        ],
    }
    nrfi, yrfi, book = odds_mod._extract_nrfi_yrfi_outcomes(payload)
    assert nrfi == -125
    assert yrfi == +105
    assert book == "draftkings"


def test_extract_nrfi_yrfi_skips_non_half_lines():
    """Some books post 0.5 *and* 1.5 lines; we only care about 0.5."""
    payload = {
        "bookmakers": [
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "totals_1st_1_innings",
                        "outcomes": [
                            {"name": "Under", "point": 1.5, "price": -180},
                            {"name": "Over",  "point": 1.5, "price": +150},
                            {"name": "Under", "point": 0.5, "price": -110},
                            {"name": "Over",  "point": 0.5, "price": -110},
                        ],
                    },
                ],
            },
        ],
    }
    nrfi, yrfi, book = odds_mod._extract_nrfi_yrfi_outcomes(payload)
    assert nrfi == -110
    assert yrfi == -110
    assert book == "fanduel"


def test_extract_nrfi_yrfi_skips_non_inning_market_keys():
    """If the per-event response only carries h2h or other markets,
    we return (None, None, '') so the caller skips the event."""
    payload = {
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Boston Red Sox", "price": -120},
                            {"name": "New York Yankees", "price": +100},
                        ],
                    },
                ],
            },
        ],
    }
    nrfi, yrfi, book = odds_mod._extract_nrfi_yrfi_outcomes(payload)
    assert nrfi is None
    assert yrfi is None


def test_extract_nrfi_yrfi_handles_empty_bookmakers():
    nrfi, yrfi, book = odds_mod._extract_nrfi_yrfi_outcomes({"bookmakers": []})
    assert nrfi is None
    assert yrfi is None
    assert book == ""


# ---------------------------------------------------------------------------
# Event date filtering and game matching
# ---------------------------------------------------------------------------


def test_events_for_date_filters_by_iso_prefix():
    events = [
        {"id": "a", "commence_time": "2026-04-29T23:05:00Z"},
        {"id": "b", "commence_time": "2026-04-30T00:35:00Z"},
        {"id": "c", "commence_time": "2026-04-29T19:35:00Z"},
        {"id": "d", "commence_time": ""},
    ]
    out = odds_mod._events_for_date(events, "2026-04-29")
    assert {e["id"] for e in out} == {"a", "c"}


def test_match_event_to_game_pk_succeeds_on_team_match():
    games = pd.DataFrame([
        {"game_pk": 777001, "home_team": "BOS", "away_team": "NYY"},
        {"game_pk": 777002, "home_team": "LAD", "away_team": "SF"},
    ])
    event = {
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
    }
    assert odds_mod._match_event_to_game_pk(event, games) == 777001


def test_match_event_to_game_pk_returns_none_on_unknown_team():
    games = pd.DataFrame([
        {"game_pk": 777001, "home_team": "BOS", "away_team": "NYY"},
    ])
    event = {"home_team": "Madrid CF", "away_team": "Real Madrid"}
    assert odds_mod._match_event_to_game_pk(event, games) is None


def test_match_event_to_game_pk_returns_none_when_pairing_missing():
    """Matching teams in the wrong home/away orientation must NOT match
    — game_pk is direction-sensitive."""
    games = pd.DataFrame([
        {"game_pk": 777001, "home_team": "BOS", "away_team": "NYY"},
    ])
    event = {
        "home_team": "New York Yankees",  # swapped
        "away_team": "Boston Red Sox",
    }
    assert odds_mod._match_event_to_game_pk(event, games) is None


# ---------------------------------------------------------------------------
# capture_closing_lines — end-to-end with mocked httpx
# ---------------------------------------------------------------------------


def test_capture_closing_lines_writes_snapshots(monkeypatch):
    games = pd.DataFrame([
        {"game_pk": 700001, "home_team": "BOS", "away_team": "NYY"},
        {"game_pk": 700002, "home_team": "LAD", "away_team": "SF"},
    ])
    store = _FakeStore(games_df=games)

    client = _FakeClient()
    # 1) Event list response.
    client.add_response([
        {
            "id": "evt-bos-nyy",
            "commence_time": "2026-04-29T23:05:00Z",
            "home_team": "Boston Red Sox",
            "away_team": "New York Yankees",
        },
        {
            "id": "evt-lad-sf",
            "commence_time": "2026-04-29T22:10:00Z",
            "home_team": "Los Angeles Dodgers",
            "away_team": "San Francisco Giants",
        },
        {
            "id": "evt-other-day",
            "commence_time": "2026-04-30T01:35:00Z",
            "home_team": "Houston Astros",
            "away_team": "Texas Rangers",
        },
    ])
    # 2) Per-event odds responses (only the two on 2026-04-29).
    client.add_response({
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "totals_1st_1_innings", "outcomes": [
                    {"name": "Under", "point": 0.5, "price": -130},
                    {"name": "Over",  "point": 0.5, "price": +110},
                ]},
            ]},
        ],
    })
    client.add_response({
        "bookmakers": [
            {"key": "fanduel", "markets": [
                {"key": "totals_1st_1_innings", "outcomes": [
                    {"name": "Under", "point": 0.5, "price": -115},
                    {"name": "Over",  "point": 0.5, "price": -105},
                ]},
            ]},
        ],
    })

    n = odds_mod.capture_closing_lines(
        store, "2026-04-29",
        http_client=client, api_key="test-key",
    )
    assert n == 4    # 2 games × NRFI + YRFI

    upserts = [u for u in store.upserts if u[0] == "nrfi_odds_snapshot"]
    assert len(upserts) == 1
    rows = upserts[0][1]
    by_pk_market = {(r["game_pk"], r["market_type"]): r for r in rows}
    assert by_pk_market[(700001, "NRFI")]["american_odds"] == -130
    assert by_pk_market[(700001, "NRFI")]["book"] == "draftkings"
    assert by_pk_market[(700001, "YRFI")]["american_odds"] == +110
    assert by_pk_market[(700002, "NRFI")]["american_odds"] == -115
    assert by_pk_market[(700002, "YRFI")]["book"] == "fanduel"
    # decimal_odds populated
    for r in rows:
        assert r["decimal_odds"] > 1.0
    # snapshot_kind tag matches what settle expects.
    assert all(r["snapshot_kind"] == "closing" for r in rows)


def test_capture_closing_lines_skips_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    store = _FakeStore(games_df=pd.DataFrame([
        {"game_pk": 1, "home_team": "BOS", "away_team": "NYY"},
    ]))
    n = odds_mod.capture_closing_lines(store, "2026-04-29")
    assert n == 0
    assert not [u for u in store.upserts if u[0] == "nrfi_odds_snapshot"]


def test_capture_closing_lines_swallows_event_list_error():
    games = pd.DataFrame([
        {"game_pk": 1, "home_team": "BOS", "away_team": "NYY"},
    ])
    store = _FakeStore(games_df=games)
    client = _FakeClient()
    client.raise_on_next(RuntimeError("HTTP 429 — quota exceeded"))

    n = odds_mod.capture_closing_lines(
        store, "2026-04-29", http_client=client, api_key="test-key",
    )
    assert n == 0
    assert not [u for u in store.upserts if u[0] == "nrfi_odds_snapshot"]


def test_capture_closing_lines_skips_unmatched_events():
    games = pd.DataFrame([
        {"game_pk": 1, "home_team": "BOS", "away_team": "NYY"},
    ])
    store = _FakeStore(games_df=games)

    client = _FakeClient()
    client.add_response([
        {
            "id": "evt-unknown",
            "commence_time": "2026-04-29T23:05:00Z",
            "home_team": "Madrid CF",
            "away_team": "Barcelona FC",
        },
    ])

    n = odds_mod.capture_closing_lines(
        store, "2026-04-29", http_client=client, api_key="test-key",
    )
    assert n == 0


def test_capture_closing_lines_no_games_in_db_short_circuits():
    store = _FakeStore(games_df=pd.DataFrame())
    client = _FakeClient()
    n = odds_mod.capture_closing_lines(
        store, "2026-04-29", http_client=client, api_key="test-key",
    )
    assert n == 0
    # We should NOT have hit the API at all if there are no games.
    assert client.calls == []


def test_capture_closing_lines_partial_event_failure_keeps_others():
    """If one per-event fetch fails, the rest of the slate still lands."""
    games = pd.DataFrame([
        {"game_pk": 700001, "home_team": "BOS", "away_team": "NYY"},
        {"game_pk": 700002, "home_team": "LAD", "away_team": "SF"},
    ])
    store = _FakeStore(games_df=games)

    client = _FakeClient()
    client.add_response([
        {
            "id": "evt-bos-nyy",
            "commence_time": "2026-04-29T23:05:00Z",
            "home_team": "Boston Red Sox",
            "away_team": "New York Yankees",
        },
        {
            "id": "evt-lad-sf",
            "commence_time": "2026-04-29T22:10:00Z",
            "home_team": "Los Angeles Dodgers",
            "away_team": "San Francisco Giants",
        },
    ])
    # First per-event call returns a 4xx-equivalent, second succeeds.
    client.add_response({"error": "rate-limited"}, status=429)
    client.add_response({
        "bookmakers": [
            {"key": "fanduel", "markets": [
                {"key": "totals_1st_1_innings", "outcomes": [
                    {"name": "Under", "point": 0.5, "price": -120},
                    {"name": "Over",  "point": 0.5, "price": +100},
                ]},
            ]},
        ],
    })

    n = odds_mod.capture_closing_lines(
        store, "2026-04-29", http_client=client, api_key="test-key",
    )
    assert n == 2
    rows = store.upserts[-1][1]
    assert {r["game_pk"] for r in rows} == {700002}


# ---------------------------------------------------------------------------
# DDL idempotency
# ---------------------------------------------------------------------------


def test_init_odds_tables_runs_ddl():
    store = _FakeStore()
    odds_mod.init_odds_tables(store)
    sql_blob = " ".join(s for s, _ in store.executed)
    assert "nrfi_odds_snapshot" in sql_blob
    assert "snapshot_kind" in sql_blob


# ---------------------------------------------------------------------------
# lookup_closing_odds
# ---------------------------------------------------------------------------


def test_lookup_closing_odds_returns_value_when_present():
    store = _FakeStore()
    store.queue_query(
        "FROM nrfi_odds_snapshot",
        pd.DataFrame([{"american_odds": -135.0}]),
    )
    out = odds_mod.lookup_closing_odds(store, 12345, "NRFI")
    assert out == pytest.approx(-135.0)


def test_lookup_closing_odds_returns_none_when_missing():
    store = _FakeStore()
    store.queue_query("FROM nrfi_odds_snapshot", pd.DataFrame())
    assert odds_mod.lookup_closing_odds(store, 99, "YRFI") is None
