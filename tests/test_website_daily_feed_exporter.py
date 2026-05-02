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


def test_load_nrfi_picks_handles_nan_market_prob():
    """``run_daily.py``'s Poisson baseline doesn't populate
    ``market_prob`` so DuckDB stores NaN; the publish step must not
    crash on those rows."""
    import math
    from edge_equation.engines.website.build_daily_feed import _load_nrfi_picks
    store = _FakeStore([_row(market_prob=math.nan)])
    picks = _load_nrfi_picks(store, "2026-05-01")
    assert len(picks) == 1
    # Falls through to the -110 default when market_prob is unusable.
    assert picks[0].line_odds == -110.0


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


def test_market_prob_to_american_handles_nan():
    """``run_daily.py`` doesn't populate ``market_prob`` on the Poisson
    baseline path, so DuckDB stores NaN; the publish step's odds
    converter must not crash on those rows."""
    import math
    from edge_equation.engines.website.build_daily_feed import (
        _market_prob_to_american,
    )
    assert _market_prob_to_american(math.nan) == -110.0
    assert _market_prob_to_american(None) == -110.0      # type: ignore[arg-type]
    assert _market_prob_to_american("garbage") == -110.0  # type: ignore[arg-type]


def test_safe_float_handles_nan_and_garbage():
    import math
    from edge_equation.engines.website.build_daily_feed import _safe_float
    assert _safe_float(math.nan) == 0.0
    assert _safe_float(None) == 0.0
    assert _safe_float("notanum") == 0.0
    assert _safe_float(0.42) == 0.42
    assert _safe_float("0.42") == 0.42


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
    """NO_PLAY rows are dropped so the public ledger never shows them.

    The exporter re-classifies every row against the current tier
    ladder before publishing — so the persisted ``tier`` column is
    informational only. We construct rows with edge values that
    map cleanly to LEAN and STRONG under the active thresholds, plus
    one row whose edge is sub-threshold and should be dropped.
    """
    from edge_equation.engines.website.build_daily_feed import _load_props_picks
    store = _FakePropsStore([
        # 3.0pp edge + 0.55 prob → LEAN under current ladder.
        _prop_row(edge_pp=3.0, model_prob=0.55, tier="LEAN",
                    player_name="Player A"),
        # 9.0pp edge + 0.65 prob → STRONG under current ladder.
        _prop_row(edge_pp=9.0, model_prob=0.65, tier="STRONG",
                    player_name="Player B"),
        # 1.0pp edge → NO_PLAY (sub-threshold), should be filtered.
        _prop_row(edge_pp=1.0, model_prob=0.40, tier="LEAN",
                    player_name="Player C"),
    ])
    picks = _load_props_picks(store, "2026-05-01")
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

    # 9.0pp edge + 0.65 model_prob re-classifies to STRONG under
    # the current ladder; persisted tier is informational only.
    store = _FakePropsStore([_prop_row(edge_pp=9.0, model_prob=0.65,
                                          tier="STRONG")])
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


def test_props_query_filters_confidence_above_pure_prior():
    """The SQL must reject ``confidence == 0.30`` rows so historical
    pure-prior picks stored before the orchestrator's confidence floor
    was added don't leak into the public feed."""
    from edge_equation.engines.website.build_daily_feed import _TODAY_PROPS_QUERY
    sql = _TODAY_PROPS_QUERY.lower()
    assert "confidence > 0.30" in sql or "confidence > 0.3" in sql


def test_fullgame_query_filters_confidence_above_pure_prior():
    """Same belt-and-suspenders SQL filter for full-game predictions."""
    from edge_equation.engines.website.build_daily_feed import _TODAY_FULLGAME_QUERY
    sql = _TODAY_FULLGAME_QUERY.lower()
    assert "confidence > 0.30" in sql or "confidence > 0.3" in sql


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


# ---------------------------------------------------------------------------
# Full-Game extension
# ---------------------------------------------------------------------------


class _FakeFullGameStore:
    """Mimics FullGameStore.query_df for the full-game exporter tests."""

    def __init__(self, rows: list[dict] | None = None,
                  has_table: bool = True):
        self._rows = rows or []
        self._has_table = has_table

    def query_df(self, sql: str, params: tuple = ()):
        import pandas as pd
        if "LIMIT 1" in sql:
            if "FROM fullgame_predictions" in sql and not self._has_table:
                raise RuntimeError("fullgame_predictions missing")
            return pd.DataFrame([{"col": 1}])
        return pd.DataFrame(self._rows)


def _fg_row(**overrides: Any) -> dict:
    base = {
        "game_pk": 778899,
        "market_type": "Total",
        "side": "Over",
        "team_tricode": "",
        "line_value": 8.5,
        "model_prob": 0.55,
        "market_prob": 0.50,
        "edge_pp": 4.5,
        "american_odds": -110,
        "book": "draftkings",
        "confidence": 0.65,
        "tier": "STRONG",
        "feature_blob": '{"lam_used": 9.10}',
        "event_date": "2026-05-01",
    }
    base.update(overrides)
    return base


def test_fullgame_total_uses_TOTAL_market_type():
    """Full-Game classifier groups Total picks under MONEYLINE/TOTAL/etc."""
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([_fg_row(market_type="Total")])
    picks = _load_fullgame_picks(store, "2026-05-01")
    assert len(picks) == 1
    assert picks[0].market_type == "TOTAL"


def test_fullgame_ml_uses_MONEYLINE_market_type():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([_fg_row(market_type="ML", side="NYY",
                                          team_tricode="NYY", line_value=0.0)])
    pick = _load_fullgame_picks(store, "2026-05-01")[0]
    assert pick.market_type == "MONEYLINE"


def test_fullgame_run_line_uses_RUN_LINE_market_type():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([_fg_row(market_type="Run_Line", side="NYY",
                                          team_tricode="NYY", line_value=-1.5)])
    pick = _load_fullgame_picks(store, "2026-05-01")[0]
    assert pick.market_type == "RUN_LINE"


def test_fullgame_total_selection_label_is_human_readable():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([_fg_row(market_type="Total", side="Over",
                                          line_value=8.5)])
    pick = _load_fullgame_picks(store, "2026-05-01")[0]
    assert "Total Runs Over 8.5" in pick.selection


def test_fullgame_ml_selection_label_includes_team():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([_fg_row(market_type="ML", side="NYY",
                                          team_tricode="NYY", line_value=0.0)])
    pick = _load_fullgame_picks(store, "2026-05-01")[0]
    assert "NYY" in pick.selection
    assert "Moneyline" in pick.selection


def test_fullgame_run_line_selection_includes_signed_spread():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([
        _fg_row(market_type="Run_Line", side="NYY", team_tricode="NYY",
                  line_value=-1.5),
        _fg_row(market_type="Run_Line", side="BOS", team_tricode="BOS",
                  line_value=+1.5),
    ])
    picks = _load_fullgame_picks(store, "2026-05-01")
    by_team = {p.selection.split(" · ")[0]: p.selection for p in picks}
    assert "-1.5" in by_team["NYY"]
    assert "+1.5" in by_team["BOS"]


def test_fullgame_pick_grade_follows_tier_mapping():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    # 9.0pp edge + 0.65 model_prob re-classifies to STRONG.
    store = _FakeFullGameStore([_fg_row(edge_pp=9.0, model_prob=0.65,
                                          tier="STRONG")])
    pick = _load_fullgame_picks(store, "2026-05-01")[0]
    assert pick.grade == "A"
    assert pick.tier == "STRONG"


def test_fullgame_edge_serialized_as_fraction_string():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([_fg_row(edge_pp=4.5)])
    pick = _load_fullgame_picks(store, "2026-05-01")[0]
    assert pick.edge == "0.0450"
    assert pick.fair_prob == "0.5500"


def test_fullgame_ml_omits_line_number():
    """ML / F5_ML picks have no line value to render."""
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([_fg_row(market_type="ML", side="NYY",
                                          team_tricode="NYY", line_value=0.0)])
    pick = _load_fullgame_picks(store, "2026-05-01")[0]
    assert pick.to_dict()["line"]["number"] is None


def test_fullgame_total_includes_line_number():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore([_fg_row(market_type="Total", line_value=8.5)])
    pick = _load_fullgame_picks(store, "2026-05-01")[0]
    assert pick.to_dict()["line"]["number"] == "8.5"


def test_fullgame_returns_empty_when_table_missing():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    store = _FakeFullGameStore(has_table=False)
    assert _load_fullgame_picks(store, "2026-05-01") == []


def test_fullgame_returns_empty_when_store_is_none():
    from edge_equation.engines.website.build_daily_feed import _load_fullgame_picks
    assert _load_fullgame_picks(None, "2026-05-01") == []


def test_build_bundle_combines_nrfi_props_fullgame():
    from edge_equation.engines.website.build_daily_feed import build_bundle
    nrfi_store = _FakeStore([_row()])
    props_store = _FakePropsStore([_prop_row()])
    fg_store = _FakeFullGameStore([_fg_row()])
    bundle = build_bundle(
        nrfi_store, "2026-05-01",
        props_store=props_store,
        fullgame_store=fg_store,
    )
    market_types = {p.market_type for p in bundle.picks}
    assert "NRFI" in market_types or "YRFI" in market_types
    assert "PLAYER_PROP_HR" in market_types
    assert "TOTAL" in market_types
