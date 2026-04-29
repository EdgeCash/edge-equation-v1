"""Parlay engine — builds tier-gated, correlation-adjusted parlay
candidates and persists them to a units-only ledger.

See `builder.py` for the construction pipeline, `correlations.py`
for the same-game / same-player correlation table, and `ledger.py`
for the persistence layer.
"""

from __future__ import annotations

from .builder import (
    ParlayCandidate,
    ParlayLeg,
    build_parlay_candidates,
    expected_value_units,
    qualify_parlay,
    render_candidate,
    simulate_correlated_joint_prob,
)
from .config import ParlayConfig, load_from_env
from .correlations import (
    ParlayLegContext,
    are_mutually_exclusive,
    correlation_for_pair,
)
from .ledger import (
    get_ledger,
    init_parlay_tables,
    record_parlay,
    render_ledger_section,
    settle_parlay,
)

__all__ = [
    # builder
    "ParlayCandidate",
    "ParlayLeg",
    "build_parlay_candidates",
    "expected_value_units",
    "qualify_parlay",
    "render_candidate",
    "simulate_correlated_joint_prob",
    # config
    "ParlayConfig",
    "load_from_env",
    # correlations
    "ParlayLegContext",
    "are_mutually_exclusive",
    "correlation_for_pair",
    # ledger
    "get_ledger",
    "init_parlay_tables",
    "record_parlay",
    "render_ledger_section",
    "settle_parlay",
]
