"""Tests for the daily-feed exporter (today's NRFI picks → latest.json).

Synthetic input only — fakes the NRFIStore so we don't need DuckDB.
Verifies the predictions→FeedPick mapping, NRFI/YRFI side selection,
JSON shape, the no-picks empty state, and the helper math.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


pytest.importorskip("pandas")


# ---------------------------------------------------------------------------
# Fake store
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, predictions: list[dict] | None = None,
                  has_predictions: bool = True,
                  has_games: bool = True):
        self._predictions = predictions or []
        self._has_predictions = has_predictions
        self._has_games = has_games

    def query_df(self, sql: str, params: tuple = ()):
        import pandas as pd
        # _table_exists probe: "SELECT 1 FROM <table> LIMIT 1"
        if "LIMIT 1" in sql:
            if "FROM predictions" in sql and not self._has_predictions:
                raise RuntimeError("predictions missing")
            if "FROM games" in sql and not self._has_games:
                raise RuntimeError("games missing")
            return pd.DataFrame([{"col": 1}])
        # The real query — return whatever we were primed with.
        return pd.DataFrame(self._predictions)


def _row(**overrides: Any) -> dict:
    """A predictions × games joined row, schema matching
    build_daily_feed._TODAY_NRFI_QUERY's column aliases."""
    base = {
        "game_pk": 778899,
        "nrfi_prob": 0.62,
        "nrfi_pct": 62.0,
        "lambda_total": 0.92,
        "color_band": "MODERATE",
        "market_prob": 0.55,
        "edge": 0.07,
        "kelly_units": 0.012,
        "away_team": "NYY",
        "home_team": "BOS",
        "first_pitch_ts": "2026-05-01T19:10:00Z",
        "game_date": "2026-05-01",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Side selection — NRFI vs YRFI
# ---------------------------------------------------------------------------


def test_nrfi_picked_when_prob_at_or_above_50():
    from edge_equation.engines.website.build_daily_feed import _load_nrfi_picks
    store = _FakeStore([_row(nrfi_prob=0.62)])
    picks = _load_nrfi_picks(store, "2026-05-01")
    assert len(picks) == 1
    assert picks[0].market_type == "NRFI"
    assert picks[0].fair_prob == "0.6200"


def test_yrfi_picked_and_prob_flipped_when_below_50():
    from edge_equation.engines.website.build_daily_feed import _load_nrfi_picks
    store = _FakeStore([_row(nrfi_prob=0.40)])
    picks = _load_nrfi_picks(store, "2026-05-01")
    assert picks[0].market_type == "YRFI"
    # Side prob = 1 - nrfi_prob = 0.60
    assert picks[0].fair_prob == "0.6000"


def test_selection_label_is_human_readable():
    from edge_equation.engines.website.build_daily_feed import _load_nrfi_picks
    store = _FakeStore([_row(away_team="NYY", home_team="BOS")])
    pick = _load_nrfi_picks(store, "2026-05-01")[0]
    assert "NYY @ BOS" in pick.selection
    assert pick.selection.startswith("NRFI ·")


# ---------------------------------------------------------------------------
# Empty / missing-table behavior
# ---------------------------------------------------------------------------


def test_returns_empty_list_when_predictions_table_missing():
    from edge_equation.engines.website.build_daily_feed import _load_nrfi_picks
    store = _FakeStore(has_predictions=False)
    assert _load_nrfi_picks(store, "2026-05-01") == []


def test_returns_empty_list_when_no_rows_for_target_date():
    from edge_equation.engines.website.build_daily_feed import _load_nrfi_picks
    store = _FakeStore([])
    assert _load_nrfi_picks(store, "2026-05-01") == []


def test_build_bundle_emits_friendly_notes_when_empty():
    from edge_equation.engines.website.build_daily_feed import build_bundle
    bundle = build_bundle(_FakeStore([]), "2026-05-01")
    assert bundle.picks == []
    assert "no picks for this slate" in bundle.notes.lower()


# ---------------------------------------------------------------------------
# Output JSON shape
# ---------------------------------------------------------------------------


def test_write_bundle_produces_v1_schema_json(tmp_path: Path):
    from edge_equation.engines.website.build_daily_feed import (
        build_bundle, write_bundle,
    )
    store = _FakeStore([_row()])
    bundle = build_bundle(store, "2026-05-01")
    out = tmp_path / "latest.json"
    write_bundle(bundle, out)
    payload = json.loads(out.read_text())
    assert payload["version"] == 1
    assert payload["date"] == "2026-05-01"
    assert payload["source"] == "run_daily.py"
    assert isinstance(payload["picks"], list)
    pick = payload["picks"][0]
    # Schema fields the website's daily-edge.tsx expects.
    for key in ("id", "sport", "market_type", "selection", "line",
                "fair_prob", "edge", "kelly", "grade", "notes",
                "event_time", "game_id"):
        assert key in pick


def test_grade_thresholds_match_documented_boundaries():
    """A+ at >=70%, A at >=64%, B at >=58%, C at >=55%, D at >=50%, else F."""
    from edge_equation.engines.website.build_daily_feed import (
        _grade_from_probability,
    )
    assert _grade_from_probability(0.71) == "A+"
    assert _grade_from_probability(0.65) == "A"
    assert _grade_from_probability(0.59) == "B"
    assert _grade_from_probability(0.56) == "C"
    assert _grade_from_probability(0.51) == "D"
    assert _grade_from_probability(0.45) == "F"


def test_market_prob_to_american_handles_favorites_and_dogs():
    from edge_equation.engines.website.build_daily_feed import (
        _market_prob_to_american,
    )
    # Favorite (>=50%) → negative odds
    assert _market_prob_to_american(0.60) < 0
    # Dog (<50%) → positive odds
    assert _market_prob_to_american(0.40) > 0
    # Pathological inputs default to -110
    assert _market_prob_to_american(0.0) == -110.0
    assert _market_prob_to_american(1.0) == -110.0


def test_market_prob_to_american_known_values():
    """Spot-check a few known conversions."""
    from edge_equation.engines.website.build_daily_feed import (
        _market_prob_to_american,
    )
    # 50% = -100 (the formula edge case maps via the >=50% branch)
    assert _market_prob_to_american(0.50) == -100
    # 60% should land near -150
    assert _market_prob_to_american(0.60) == -150
    # 40% should land at +150
    assert _market_prob_to_american(0.40) == 150


# ---------------------------------------------------------------------------
# Notes formatting
# ---------------------------------------------------------------------------


def test_notes_include_side_pct_and_lambda():
    from edge_equation.engines.website.build_daily_feed import _load_nrfi_picks
    store = _FakeStore([_row(nrfi_prob=0.65, lambda_total=0.85)])
    pick = _load_nrfi_picks(store, "2026-05-01")[0]
    assert "65.0% NRFI" in pick.notes
    assert "λ=0.85" in pick.notes
