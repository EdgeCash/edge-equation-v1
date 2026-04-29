"""Tests for ParlayConfig defaults + env-var overrides."""

from __future__ import annotations

import pytest

from edge_equation.engines.parlay.config import (
    ENV_DEFAULT_STAKE, ENV_MAX_LEGS, ENV_MC_TRIALS, ENV_MIN_EV_UNITS,
    ENV_MIN_JOINT_PROB, ENV_MIN_TIER, ParlayConfig, load_from_env,
)
from edge_equation.engines.tiering import Tier


def test_default_config_matches_audit_policy():
    cfg = ParlayConfig()
    assert cfg.min_tier == Tier.STRONG
    assert cfg.max_legs == 3
    assert cfg.default_stake_units == 0.5
    assert cfg.min_joint_prob == 0.68
    assert cfg.min_ev_units == 0.25


def test_load_from_env_with_no_overrides_matches_defaults(monkeypatch):
    for v in (ENV_MIN_TIER, ENV_MAX_LEGS, ENV_DEFAULT_STAKE,
                ENV_MIN_JOINT_PROB, ENV_MIN_EV_UNITS, ENV_MC_TRIALS):
        monkeypatch.delenv(v, raising=False)
    cfg = load_from_env()
    base = ParlayConfig()
    assert cfg.min_tier == base.min_tier
    assert cfg.max_legs == base.max_legs
    assert cfg.default_stake_units == base.default_stake_units


def test_env_override_max_legs(monkeypatch):
    """Allow 4 legs only via PARLAY_MAX_LEGS=4 — never default."""
    monkeypatch.setenv(ENV_MAX_LEGS, "4")
    assert load_from_env().max_legs == 4


def test_env_override_min_tier_uppercases(monkeypatch):
    """Tier strings should be tolerated case-insensitively."""
    monkeypatch.setenv(ENV_MIN_TIER, "lock")
    assert load_from_env().min_tier == Tier.LOCK


def test_env_override_min_tier_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(ENV_MIN_TIER, "ULTRALOCK")
    assert load_from_env().min_tier == Tier.STRONG


def test_env_override_default_stake(monkeypatch):
    monkeypatch.setenv(ENV_DEFAULT_STAKE, "0.25")
    assert load_from_env().default_stake_units == pytest.approx(0.25)


def test_env_override_min_joint_prob(monkeypatch):
    monkeypatch.setenv(ENV_MIN_JOINT_PROB, "0.72")
    assert load_from_env().min_joint_prob == pytest.approx(0.72)


def test_env_override_min_ev(monkeypatch):
    monkeypatch.setenv(ENV_MIN_EV_UNITS, "0.10")
    assert load_from_env().min_ev_units == pytest.approx(0.10)


def test_env_override_invalid_int_falls_back(monkeypatch):
    monkeypatch.setenv(ENV_MAX_LEGS, "not-a-number")
    assert load_from_env().max_legs == 3


def test_env_override_invalid_float_falls_back(monkeypatch):
    monkeypatch.setenv(ENV_DEFAULT_STAKE, "not-a-number")
    assert load_from_env().default_stake_units == pytest.approx(0.5)


def test_env_override_empty_string_falls_back(monkeypatch):
    monkeypatch.setenv(ENV_MAX_LEGS, "")
    assert load_from_env().max_legs == 3
