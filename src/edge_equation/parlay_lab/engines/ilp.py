"""ILP engine -- thin wrapper around the production ILP strategy.

The shared algorithm now lives in
``edge_equation.engines.parlay.strategies.build_ilp``; this class
just adapts it to the ParlayEngine ABC the shootout uses. Keeps
parlay_lab and production on a single source of truth.

Tests that previously patched ``parlay_lab.engines.ilp`` internals
(``_PULP_OK``, ``pulp``) should patch the same symbols on
``edge_equation.engines.parlay.strategies`` instead.
"""

from __future__ import annotations

from edge_equation.engines.parlay.builder import ParlayCandidate, ParlayLeg
from edge_equation.engines.parlay.config import ParlayConfig
from edge_equation.engines.parlay import strategies as _strategies

from ..base import ParlayEngine


# Re-export the PuLP-presence flag so tests (and the engine's own
# fallback path) can patch a single symbol regardless of which
# entry point they came in through.
_PULP_OK = _strategies._PULP_OK
pulp = _strategies.pulp


class ILPEngine(ParlayEngine):
    name = "ilp"
    description = (
        "ILP (CBC) optimum: maximize sum of log(p*d) under exactly-k-"
        "legs + same-game cap + same-player cap, then re-evaluate the "
        "picked subset through the standard copula MC. One candidate "
        "per parlay size; replaces enumeration entirely."
    )

    def build(
        self,
        legs: list[ParlayLeg],
        config: ParlayConfig,
    ) -> list[ParlayCandidate]:
        return _strategies.build_ilp(legs, config)
