"""Tests for the closing-line snapshot logger.

Network calls (`fetch_odds_payload`) aren't exercised in CI -- they
need a real Odds API key and we don't pay credits to run tests. The
parsing + writer logic IS unit-tested against synthetic Odds API
payloads that mirror the real shape we observed in the existing
adapters.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_equation.exporters.closing_lines.snapshot import (
    SPORT_KEYS, SnapshotResult, _american_to_decimal,
    _normalize_side_label, _season_from_commence, append_snapshot,
    fetch_odds_payload, normalize_payload,
)


# ---------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------

def test_american_to_decimal_at_minus_110():
    assert _american_to_decimal(-110) == pytest.approx(1.909, abs=1e-3)


def test_american_to_decimal_at_plus_120():
    assert _american_to_decimal(120) == pytest.approx(2.20, abs=1e-3)


def test_american_to_decimal_at_zero_safe():
    assert _american_to_decimal(0) == 1.0


def test_normalize_side_h2h():
    assert _normalize_side_label("h2h", "Boston Red Sox", home="Boston Red Sox", away="NYY") == "home"
    assert _normalize_side_label("h2h", "NYY", home="Boston Red Sox", away="NYY") == "away"


def test_normalize_side_totals():
    assert _normalize_side_label("totals", "Over", home="X", away="Y") == "over"
    assert _normalize_side_label("totals", "Under", home="X", away="Y") == "under"


def test_normalize_side_unknown_team_safe():
    """Unmapped team names fall through to a slug -- never raise."""
    out = _normalize_side_label("h2h", "Unknown Team", home="A", away="B")
    assert out == "unknown_team"


def test_season_from_commence_iso_with_z():
    assert _season_from_commence("2025-08-15T19:05:00Z") == 2025


def test_season_from_commence_iso_with_offset():
    assert _season_from_commence("2024-11-01T00:00:00+00:00") == 2024


def test_season_from_commence_handles_none():
    assert _season_from_commence(None) is None


def test_season_from_commence_handles_garbage():
    assert _season_from_commence("not-a-date") is None


# ---------------------------------------------------------------------
# normalize_payload
# ---------------------------------------------------------------------

def _synth_event() -> dict:
    """One event with two books, three markets each. Mirrors the real
    Odds API shape we'd get from /v4/sports/baseball_mlb/odds."""
    return {
        "id": "evt1",
        "commence_time": "2025-08-15T19:05:00Z",
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "bookmakers": [
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Boston Red Sox", "price": -150},
                            {"name": "New York Yankees", "price": 130},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Boston Red Sox", "price": 105, "point": -1.5},
                            {"name": "New York Yankees", "price": -125, "point": 1.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 8.5},
                            {"name": "Under", "price": -110, "point": 8.5},
                        ],
                    },
                ],
            },
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Boston Red Sox", "price": -145},
                            {"name": "New York Yankees", "price": 125},
                        ],
                    },
                ],
            },
        ],
    }


def test_normalize_payload_yields_one_row_per_outcome():
    rows = normalize_payload(
        [_synth_event()], sport="mlb", captured_at="2025-08-15T18:00:00+00:00",
    )
    # FD has 6 outcomes (2+2+2), DK has 2 -> 8 rows.
    assert len(rows) == 8


def test_normalize_payload_carries_captured_at_and_sport():
    rows = normalize_payload(
        [_synth_event()], sport="mlb", captured_at="2025-08-15T18:00:00+00:00",
    )
    assert all(r["captured_at"] == "2025-08-15T18:00:00+00:00" for r in rows)
    assert all(r["sport"] == "mlb" for r in rows)
    assert all(r["sport_key"] == "baseball_mlb" for r in rows)


def test_normalize_payload_decimal_odds_match_american():
    rows = normalize_payload(
        [_synth_event()], sport="mlb", captured_at="now",
    )
    fd_ml_home = next(
        r for r in rows
        if r["book"] == "fanduel" and r["market"] == "moneyline"
        and r["side"] == "home"
    )
    assert fd_ml_home["american_odds"] == -150
    assert fd_ml_home["decimal_odds"] == pytest.approx(1.667, abs=1e-3)


def test_normalize_payload_skips_event_without_id_or_teams():
    bad = {"commence_time": "2025-08-15T19:05:00Z"}
    rows = normalize_payload([bad], sport="mlb", captured_at="now")
    assert rows == []


def test_normalize_payload_skips_outcome_without_price():
    event = _synth_event()
    event["bookmakers"][0]["markets"][0]["outcomes"][0].pop("price")
    rows = normalize_payload([event], sport="mlb", captured_at="now")
    # 1 outcome dropped; remaining 7 stay.
    assert len(rows) == 7


def test_normalize_payload_skips_unknown_market_key():
    event = _synth_event()
    event["bookmakers"][0]["markets"].append({
        "key": "alternate_run_line",
        "outcomes": [{"name": "Boston Red Sox", "price": 200, "point": -2.5}],
    })
    rows = normalize_payload([event], sport="mlb", captured_at="now")
    # +0 rows because the unknown market key is dropped.
    assert len(rows) == 8


# ---------------------------------------------------------------------
# append_snapshot
# ---------------------------------------------------------------------

def _row(**overrides) -> dict:
    base = {
        "captured_at": "2025-08-15T18:00:00+00:00",
        "season": 2025, "sport": "mlb", "sport_key": "baseball_mlb",
        "event_id": "evt1", "scheduled_start": "2025-08-15T19:05:00Z",
        "away_team": "NYY", "home_team": "BOS",
        "market": "moneyline", "side": "home", "line": None,
        "decimal_odds": 1.667, "american_odds": -150, "book": "fanduel",
    }
    base.update(overrides)
    return base


def test_append_snapshot_writes_per_sport_per_season(tmp_path: Path):
    rows = [
        _row(sport="mlb", season=2025),
        _row(sport="mlb", season=2025),
        _row(sport="wnba", season=2025),
    ]
    stats = append_snapshot(rows, output_dir=tmp_path)
    assert stats["total_rows"] == 3
    assert stats["skipped"] == 0
    files = list(tmp_path.rglob("*.jsonl"))
    rel_names = sorted(p.name for p in files)
    assert rel_names == ["2025.jsonl", "2025.jsonl"]
    sports = sorted(p.parent.name for p in files)
    assert sports == ["mlb", "wnba"]


def test_append_snapshot_is_append_only(tmp_path: Path):
    """Two separate calls should both land lines in the same file."""
    rows1 = [_row(captured_at="t1"), _row(captured_at="t1")]
    rows2 = [_row(captured_at="t2")]
    append_snapshot(rows1, output_dir=tmp_path)
    append_snapshot(rows2, output_dir=tmp_path)
    p = tmp_path / "mlb" / "2025.jsonl"
    lines = p.read_text().splitlines()
    assert len(lines) == 3
    captured = [json.loads(l)["captured_at"] for l in lines]
    assert captured == ["t1", "t1", "t2"]


def test_append_snapshot_skips_rows_missing_sport_or_season(tmp_path: Path):
    rows = [
        _row(),
        {"market": "moneyline"},                        # missing sport / season
        _row(sport=None),
        _row(season=None),
    ]
    stats = append_snapshot(rows, output_dir=tmp_path)
    assert stats["total_rows"] == 1
    assert stats["skipped"] == 3


# ---------------------------------------------------------------------
# Sport key registry
# ---------------------------------------------------------------------

def test_sport_keys_cover_four_target_leagues():
    assert set(SPORT_KEYS) == {"mlb", "wnba", "nba", "nhl"}
    assert SPORT_KEYS["mlb"] == "baseball_mlb"
    assert SPORT_KEYS["wnba"] == "basketball_wnba"
    assert SPORT_KEYS["nba"] == "basketball_nba"
    assert SPORT_KEYS["nhl"] == "icehockey_nhl"


# ---------------------------------------------------------------------
# fetch_odds_payload error paths (without network)
# ---------------------------------------------------------------------

def test_fetch_odds_payload_rejects_unknown_sport():
    with pytest.raises(ValueError):
        fetch_odds_payload("cricket", api_key="dummy")


def test_fetch_odds_payload_raises_when_no_key(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        fetch_odds_payload("mlb")


# ---------------------------------------------------------------------
# Top-level snapshot() best-effort behaviour
# ---------------------------------------------------------------------

def test_snapshot_captures_error_without_raising(monkeypatch, tmp_path):
    """A flaky API call must NOT abort a multi-sport cron -- the error
    is captured on the result and the function returns cleanly."""
    from edge_equation.exporters.closing_lines import snapshot as snap_mod

    def _boom(*a, **kw):
        raise RuntimeError("simulated 503")
    monkeypatch.setattr(snap_mod, "fetch_odds_payload", _boom)
    result = snap_mod.snapshot("mlb", output_dir=tmp_path)
    assert isinstance(result, SnapshotResult)
    assert result.error is not None
    assert "simulated 503" in result.error
    assert result.n_rows == 0


def test_snapshot_writes_when_payload_normalises(monkeypatch, tmp_path):
    """Wire fetch -> normalise -> append: should land rows on disk."""
    from edge_equation.exporters.closing_lines import snapshot as snap_mod
    monkeypatch.setattr(
        snap_mod, "fetch_odds_payload", lambda *a, **kw: [_synth_event()],
    )
    result = snap_mod.snapshot("mlb", output_dir=tmp_path)
    assert result.error is None
    assert result.n_events == 1
    assert result.n_rows == 8
    files = list(tmp_path.rglob("*.jsonl"))
    assert len(files) == 1
    assert sum(1 for _ in files[0].open()) == 8
