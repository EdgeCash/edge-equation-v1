"""Storage-layer tests for the props engine.

DuckDB isn't installed in the lighter test environment, so we only
verify the schema strings + smoke-import without instantiating
PropsStore. The real DDL execution is exercised in the integration
test environment when duckdb is present.
"""

from __future__ import annotations

import pytest


def test_storage_module_exports_class():
    from edge_equation.engines.props_prizepicks.data import storage
    assert hasattr(storage, "PropsStore")
    assert hasattr(storage, "_SCHEMA")


def test_schema_creates_three_expected_tables():
    """Schema is the contract for the rest of the engine — make sure
    the three tables we promised above the file are actually wired."""
    from edge_equation.engines.props_prizepicks.data.storage import _SCHEMA
    blob = " ".join(_SCHEMA)
    assert "prop_predictions" in blob
    assert "prop_actuals" in blob
    assert "prop_features" in blob


def test_schema_predictions_keys_match_design():
    """Primary key on (event_date, market_type, player_name, line_value, side)
    keeps idempotent re-runs cheap (overwrite, not duplicate)."""
    from edge_equation.engines.props_prizepicks.data.storage import _SCHEMA
    pred_ddl = next(s for s in _SCHEMA if "prop_predictions" in s)
    assert "PRIMARY KEY (event_date, market_type, player_name, line_value, side)" in pred_ddl


def test_schema_actuals_key_aligns_with_settle_join():
    from edge_equation.engines.props_prizepicks.data.storage import _SCHEMA
    actuals_ddl = next(s for s in _SCHEMA if "prop_actuals" in s)
    assert "PRIMARY KEY (game_pk, market_type, player_name)" in actuals_ddl


def test_schema_features_audit_table_carries_role():
    """`role` distinguishes batter / pitcher feature blobs so the same
    player_name on different rows (rare but possible across years)
    doesn't collide on the primary key."""
    from edge_equation.engines.props_prizepicks.data.storage import _SCHEMA
    feat_ddl = next(s for s in _SCHEMA if "prop_features" in s)
    assert " role" in feat_ddl
    assert "PRIMARY KEY (event_date, player_name, role)" in feat_ddl


def test_props_store_imports_through_package():
    """Public API surface includes PropsStore."""
    from edge_equation.engines.props_prizepicks import PropsStore
    assert PropsStore is not None
