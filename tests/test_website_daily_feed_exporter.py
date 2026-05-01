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


# ---------------------------------------------------------------------------
# Props extension
# ---------------------------------------------------------------------------


class _FakePropsStore:
    """Mimics PropsStore.query_df for the props exporter tests."""

    def __init__(self, rows: list[dict] | None = None,
                  has_table: bool = True):
        self._rows = rows or []
        self._has_table = has_table

    def query_df(self, sql: str, params: tuple = ()):
        import pandas as pd
        if "LIMIT 1" in sql:
            if "FROM prop_predictions" in sql and not self._has_table:
                raise RuntimeError("prop_predictions missing")
            return pd.DataFrame([{"col": 1}])
        return pd.DataFrame(self._rows)


def _prop_row(**overrides: Any) -> dict:
    base = {
        "game_pk": 778899,
        "market_type": "HR",
        "player_name": "Aaron Judge",
        "line_value": 0.5,
        "side": "Over",
        "model_prob": 0.42,
        "market_prob": 0.36,
        "edge_pp": 6.0,
        "american_odds": 250,
        "book": "draftkings",
        "confidence": 0.65,
        "tier": "STRONG",
        "feature_blob": '{"lam": 0.21, "blend_n": 120, "confidence": 0.65}',
        "event_date": "2026-05-01",
    }
    base.update(overrides)
    return base


def test_props_picks_use_player_prop_market_type_prefix():
    """Daily-feed classifier groups Props by the PLAYER_PROP_<MARKET> prefix."""
    from edge_equation.engines.website.build_daily_feed import _load_props_picks
    store = _FakePropsStore([_prop_row()])
    picks = _load_props_picks(store, "2026-05-01")
    assert len(picks) == 1
    assert picks[0].market_type == "PLAYER_PROP_HR"


def test_props_pick_id_is_stable_and_includes_tuple():
    from edge_equation.engines.website.build_daily_feed import _load_props_picks
    store = _FakePropsStore([_prop_row(player_name="Aaron Judge",
                                         line_value=0.5, side="Over")])
    pid = _load_props_picks(store, "2026-05-01")[0].id
    assert "aaron-judge" in pid
    assert "0.5" in pid
    assert pid.endswith("-OVER")


def test_props_picks_filter_no_play_tier():
    """NO_PLAY rows are dropped so the public ledger never shows them."""
    from edge_equation.engines.website.build_daily_feed import _load_props_picks
    # The DB filter is part of the SQL; emulate that here by primitive
    # filtering — the helper trusts the store to honor the WHERE clause.
    store = _FakePropsStore([_prop_row(tier="LEAN"), _prop_row(tier="STRONG")])
    picks = _load_props_picks(store, "2026-05-01")
    # Both LEAN + STRONG are kept (NO_PLAY would have been filtered in SQL).
    assert {p.tier for p in picks} == {"LEAN", "STRONG"}


def test_props_pick_grade_follows_tier_mapping():
    from edge_equation.engines.website.build_daily_feed import (
        _grade_from_tier, _load_props_picks,
    )
    assert _grade_from_tier("ELITE") == "A+"
    assert _grade_from_tier("STRONG") == "A"
    assert _grade_from_tier("MODERATE") == "B"
    assert _grade_from_tier("LEAN") == "C"
    assert _grade_from_tier("NO_PLAY") == "F"

    store = _FakePropsStore([_prop_row(tier="STRONG")])
    pick = _load_props_picks(store, "2026-05-01")[0]
    assert pick.grade == "A"
    assert pick.tier == "STRONG"


def test_props_edge_serialized_as_fraction_string():
    """Schema requires fractional edge (0.06 = 6pp) as a string."""
    from edge_equation.engines.website.build_daily_feed import _load_props_picks
    store = _FakePropsStore([_prop_row(edge_pp=6.0)])
    pick = _load_props_picks(store, "2026-05-01")[0]
    assert pick.edge == "0.0600"
    assert pick.fair_prob == "0.4200"


def test_props_selection_label_is_human_readable():
    from edge_equation.engines.website.build_daily_feed import _load_props_picks
    store = _FakePropsStore([
        _prop_row(player_name="Aaron Judge", market_type="HR",
                    line_value=0.5, side="Over"),
        _prop_row(player_name="Mookie Betts", market_type="Total_Bases",
                    line_value=1.5, side="Over"),
    ])
    picks = _load_props_picks(store, "2026-05-01")
    by_player = {p.selection.split(" · ")[0]: p for p in picks}
    assert "Home Runs Over 0.5" in by_player["Aaron Judge"].selection
    assert "Total Bases Over 1.5" in by_player["Mookie Betts"].selection


def test_props_returns_empty_when_table_missing():
    from edge_equation.engines.website.build_daily_feed import _load_props_picks
    store = _FakePropsStore(has_table=False)
    assert _load_props_picks(store, "2026-05-01") == []


def test_props_returns_empty_when_store_is_none():
    from edge_equation.engines.website.build_daily_feed import _load_props_picks
    assert _load_props_picks(None, "2026-05-01") == []


def test_build_bundle_combines_nrfi_and_props():
    from edge_equation.engines.website.build_daily_feed import build_bundle
    nrfi_store = _FakeStore([_row()])
    props_store = _FakePropsStore([_prop_row()])
    bundle = build_bundle(nrfi_store, "2026-05-01", props_store=props_store)
    market_types = {p.market_type for p in bundle.picks}
    assert "NRFI" in market_types or "YRFI" in market_types
    assert "PLAYER_PROP_HR" in market_types
