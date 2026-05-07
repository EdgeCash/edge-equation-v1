"""Baseline engine -- thin adapter over the existing builder.

This is the "before" we're trying to beat. It calls
``build_parlay_candidates`` directly with no preprocessing. Every
new engine should match or improve on its leaderboard numbers.
"""

from __future__ import annotations

from edge_equation.engines.parlay.builder import ParlayCandidate, ParlayLeg
from edge_equation.engines.parlay.config import ParlayConfig
from edge_equation.engines.parlay.strategies import build_baseline

from ..base import ParlayEngine


class BaselineEngine(ParlayEngine):
    name = "baseline"
    description = (
        "Reference: itertools.combinations + Gaussian-copula MC + "
        "strict gate (current production)."
    )

    def build(
        self,
        legs: list[ParlayLeg],
        config: ParlayConfig,
    ) -> list[ParlayCandidate]:
        return build_baseline(legs, config)
