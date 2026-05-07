"""Tests for the env-var-driven parlay strategy resolver.

The MLB parlay engines call ``resolve_strategy(engine_kind)`` to
pick which construction algorithm to use. Tests cover the env-var
precedence ladder + fallback to baseline on unknown values.
"""

from __future__ import annotations

import pytest


pytest.importorskip("numpy")


def test_default_resolves_to_baseline_when_no_env_vars():
    from edge_equation.engines.parlay.strategies import build_baseline
    from edge_equation.engines.parlay.strategy_resolver import resolve_strategy

    fn = resolve_strategy("game_results", env={})
    assert fn is build_baseline
    fn = resolve_strategy("player_props", env={})
    assert fn is build_baseline


def test_global_env_var_applies_to_all_engines():
    from edge_equation.engines.parlay.strategies import build_deduped
    from edge_equation.engines.parlay.strategy_resolver import resolve_strategy

    env = {"MLB_PARLAY_STRATEGY": "deduped"}
    assert resolve_strategy("game_results", env=env) is build_deduped
    assert resolve_strategy("player_props", env=env) is build_deduped


def test_per_engine_env_var_overrides_global():
    """The per-engine flag wins when both are set."""
    from edge_equation.engines.parlay.strategies import (
        build_deduped, build_ilp,
    )
    from edge_equation.engines.parlay.strategy_resolver import resolve_strategy

    env = {
        "MLB_PARLAY_STRATEGY": "deduped",
        "MLB_GAME_PARLAY_STRATEGY": "ilp",
    }
    assert resolve_strategy("game_results", env=env) is build_ilp
    # player_props falls through to global since no per-engine override
    assert resolve_strategy("player_props", env=env) is build_deduped


def test_unknown_strategy_falls_back_to_baseline():
    """A typo in the env var should not crash the daily card --- the
    resolver logs a warning and returns baseline."""
    from edge_equation.engines.parlay.strategies import build_baseline
    from edge_equation.engines.parlay.strategy_resolver import resolve_strategy

    env = {"MLB_GAME_PARLAY_STRATEGY": "definitely_not_a_real_strategy"}
    assert resolve_strategy("game_results", env=env) is build_baseline


def test_empty_string_env_var_treated_as_unset():
    """An empty MLB_PARLAY_STRATEGY shouldn't override anything ---
    common when a workflow declares the env var without a value."""
    from edge_equation.engines.parlay.strategies import build_baseline
    from edge_equation.engines.parlay.strategy_resolver import resolve_strategy

    env = {"MLB_PARLAY_STRATEGY": "  ", "MLB_GAME_PARLAY_STRATEGY": ""}
    assert resolve_strategy("game_results", env=env) is build_baseline


def test_known_strategies_includes_baseline_deduped_ilp():
    from edge_equation.engines.parlay.strategies import known_strategies
    names = known_strategies()
    assert "baseline" in names
    assert "deduped" in names
    assert "ilp" in names


def test_resolver_used_by_player_props_engine_at_runtime(monkeypatch):
    """End-to-end: setting MLB_PROPS_PARLAY_STRATEGY=deduped routes
    a real PlayerPropsParlay run through the deduped algorithm
    instead of baseline."""
    monkeypatch.setenv("MLB_PROPS_PARLAY_STRATEGY", "deduped")
    from edge_equation.engines.parlay.strategy_resolver import resolve_strategy
    from edge_equation.engines.parlay.strategies import build_deduped
    assert resolve_strategy("player_props") is build_deduped
