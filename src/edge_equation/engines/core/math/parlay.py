"""Shared-core parlay builder facade.

The mature implementation lives in ``edge_equation.engines.parlay``.  This
module gives NRFI, props, and full-game engines a neutral shared-core import
path while preserving the existing parlay package and its tests.
"""

from __future__ import annotations

from edge_equation.engines.parlay import (  # noqa: F401
    ParlayCandidate,
    ParlayConfig,
    ParlayLeg,
    build_parlay_candidates,
    expected_value_units,
    qualify_parlay,
    render_candidate,
    simulate_correlated_joint_prob,
)

__all__ = [
    "ParlayCandidate",
    "ParlayConfig",
    "ParlayLeg",
    "build_parlay_candidates",
    "expected_value_units",
    "qualify_parlay",
    "render_candidate",
    "simulate_correlated_joint_prob",
]
