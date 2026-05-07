"""Tests for the commence_time roundtrip.

End-to-end: Odds API line -> PropEdgePick / FullGameEdgePick ->
PropOutput / FullGameOutput -> persistence row -> _load_props_picks /
_load_fullgame_picks -> FeedPick.event_time.

Pre-fix, ``event_time`` was hard-coded ``None`` on every prop /
fullgame FeedPick, so the bundle-boundary upcoming-only failsafe
silently dropped the entire prop slate. These tests pin the wiring
so the regression doesn't recur.
"""

from __future__ import annotations

import pytest


pytest.importorskip("numpy")


# ---------------------------------------------------------------------------
# PropEdgePick / PropOutput carry commence_time
# ---------------------------------------------------------------------------


def test_prop_edge_pick_has_commence_time_field():
    from edge_equation.engines.props_prizepicks.edge import PropEdgePick
    # Default is empty string for backwards compat with older fixtures.
    fields = {f for f in PropEdgePick.__dataclass_fields__}
    assert "commence_time" in fields


def test_prop_output_has_commence_time_field():
    from edge_equation.engines.props_prizepicks.output import PropOutput
    fields = {f for f in PropOutput.__dataclass_fields__}
    assert "commence_time" in fields


def test_build_prop_output_threads_commence_time_through():
    """The factory should propagate ``commence_time`` from pick to
    output verbatim so persistence sees the right value."""
    from edge_equation.engines.props_prizepicks.edge import PropEdgePick
    from edge_equation.engines.props_prizepicks.output import (
        build_prop_output,
    )
    from edge_equation.engines.tiering import Tier, classify_tier
    clf = classify_tier(market_type="HR", edge=0.08, side_probability=0.42)
    pick = PropEdgePick(
        market_canonical="HR", market_label="Home Runs",
        player_name="Aaron Judge", line_value=0.5, side="Over",
        model_prob=0.42, market_prob_raw=0.36, market_prob_devigged=0.34,
        vig_corrected=True, edge_pp=8.0,
        american_odds=200.0, decimal_odds=3.0, book="draftkings",
        tier=clf.tier, tier_classification=clf,
        commence_time="2026-05-08T22:35:00Z",
    )
    out = build_prop_output(pick)
    assert out.commence_time == "2026-05-08T22:35:00Z"


# ---------------------------------------------------------------------------
# FullGameEdgePick / FullGameOutput carry commence_time
# ---------------------------------------------------------------------------


def test_fullgame_edge_pick_has_commence_time_field():
    from edge_equation.engines.full_game.edge import FullGameEdgePick
    fields = {f for f in FullGameEdgePick.__dataclass_fields__}
    assert "commence_time" in fields


def test_fullgame_output_has_commence_time_field():
    from edge_equation.engines.full_game.output import FullGameOutput
    fields = {f for f in FullGameOutput.__dataclass_fields__}
    assert "commence_time" in fields


def test_build_fullgame_output_threads_commence_time_through():
    from edge_equation.engines.full_game.edge import FullGameEdgePick
    from edge_equation.engines.full_game.output import (
        build_full_game_output,
    )
    from edge_equation.engines.tiering import Tier, classify_tier
    clf = classify_tier(market_type="ML", edge=0.06, side_probability=0.55)
    pick = FullGameEdgePick(
        market_canonical="ML", market_label="Moneyline",
        home_team="New York Yankees", away_team="Boston Red Sox",
        home_tricode="NYY", away_tricode="BOS",
        side="Home", team_tricode="NYY",
        line_value=None, model_prob=0.55,
        market_prob_raw=0.51, market_prob_devigged=0.49,
        vig_corrected=True, edge_pp=6.0,
        american_odds=-120.0, decimal_odds=1.83, book="draftkings",
        tier=clf.tier, tier_classification=clf,
        commence_time="2026-05-08T23:05:00Z",
    )
    out = build_full_game_output(pick)
    assert out.commence_time == "2026-05-08T23:05:00Z"


# ---------------------------------------------------------------------------
# DuckDB schema accepts commence_time + migration is idempotent
# ---------------------------------------------------------------------------


def test_props_store_creates_commence_time_column(tmp_path):
    pytest.importorskip("duckdb")
    from edge_equation.engines.props_prizepicks.data.storage import PropsStore
    db_path = tmp_path / "test_props.duckdb"
    store = PropsStore(db_path)
    cols = [
        r[0] for r in store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'prop_predictions'",
        ).fetchall()
    ]
    assert "commence_time" in cols


def test_fullgame_store_creates_commence_time_column(tmp_path):
    pytest.importorskip("duckdb")
    from edge_equation.engines.full_game.data.storage import FullGameStore
    db_path = tmp_path / "test_fg.duckdb"
    store = FullGameStore(db_path)
    cols = [
        r[0] for r in store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'fullgame_predictions'",
        ).fetchall()
    ]
    assert "commence_time" in cols


def test_props_store_migration_is_idempotent(tmp_path):
    """Re-opening the same DuckDB twice mustn't crash --- the
    ALTER TABLE ADD COLUMN IF NOT EXISTS should silently no-op the
    second time even if the first run already added the column."""
    pytest.importorskip("duckdb")
    from edge_equation.engines.props_prizepicks.data.storage import PropsStore
    db_path = tmp_path / "idem.duckdb"
    store_a = PropsStore(db_path)
    store_a._conn.close()
    # Second open --- if the migration tried ALTER ADD COLUMN
    # unconditionally on an already-migrated DB, this would raise.
    store_b = PropsStore(db_path)
    cols = [
        r[0] for r in store_b._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'prop_predictions'",
        ).fetchall()
    ]
    assert "commence_time" in cols


# ---------------------------------------------------------------------------
# _event_time_or_none coercion in build_daily_feed
# ---------------------------------------------------------------------------


def test_event_time_or_none_handles_none_and_empty():
    from edge_equation.engines.website.build_daily_feed import (
        _event_time_or_none,
    )
    assert _event_time_or_none(None) is None
    assert _event_time_or_none("") is None
    assert _event_time_or_none("   ") is None


def test_event_time_or_none_returns_iso_string_unchanged():
    from edge_equation.engines.website.build_daily_feed import (
        _event_time_or_none,
    )
    assert _event_time_or_none("2026-05-08T22:35:00Z") == (
        "2026-05-08T22:35:00Z"
    )
    assert _event_time_or_none("  2026-05-08T22:35:00Z  ") == (
        "2026-05-08T22:35:00Z"
    )


# ---------------------------------------------------------------------------
# Smoke: an empty PropOutput round-trips persistence with commence_time
# ---------------------------------------------------------------------------


def test_props_persist_predictions_writes_commence_time(tmp_path, monkeypatch):
    """Persist one PropOutput with a known commence_time and read back
    via the store --- regression guard for the wiring in daily.py."""
    pytest.importorskip("duckdb")
    from edge_equation.engines.props_prizepicks.config import PropsConfig
    from edge_equation.engines.props_prizepicks.daily import (
        _persist_predictions,
    )
    from edge_equation.engines.props_prizepicks.output import PropOutput
    from edge_equation.engines.props_prizepicks.data.storage import PropsStore

    # PropsConfig is frozen, so build a fresh one with the path override
    # rather than mutating in place.
    from dataclasses import replace
    cfg = replace(
        PropsConfig().resolve_paths(),
        duckdb_path=tmp_path / "props_persist.duckdb",
    )
    out = PropOutput(
        game_id="evt-1", market_type="HR", market_label="Home Runs",
        player_name="Aaron Judge", line_value=0.5, side="Over",
        model_prob=0.42, model_pct=42.0, market_prob=0.34,
        market_prob_raw=0.36, vig_corrected=True,
        american_odds=200.0, decimal_odds=3.0, book="draftkings",
        tier="STRONG", grade="A",
        commence_time="2026-05-08T22:35:00Z",
    )
    _persist_predictions(cfg, [out], target_date="2026-05-08")

    store = PropsStore(cfg.duckdb_path)
    rows = store._conn.execute(
        "SELECT commence_time FROM prop_predictions "
        "WHERE event_date = '2026-05-08'",
    ).fetchall()
    assert rows == [("2026-05-08T22:35:00Z",)]
