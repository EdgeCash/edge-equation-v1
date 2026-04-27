"""Bridge between the elite NRFI engine (`nrfi/`) and the deterministic
Edge Equation core (`src/edge_equation/`).

This package exists to keep `nrfi/` self-contained while still letting it
piggyback on the math primitives that the rest of the engine has been
hardened around (Tango-style shrinkage, isotonic calibration, confidence
scoring, exponential decay). Everything here is a thin adapter — no new
math is invented; we just translate float-world NRFI inputs into the
Decimal-world primitives the core engine speaks.

Modules
-------
shrinkage  : Tango-style empirical-Bayes shrinkage helpers built on top
             of the Decimal math layer's blending pattern.
calibration: Decimal-aware isotonic wrapper that piggybacks on
             `edge_equation.math.isotonic.IsotonicRegressor`.
grading    : Convert blended P(NRFI) + edge into the engine's grade /
             realization-bucket conventions via `ConfidenceScorer`.
engine_bridge : `NRFIEngineBridge` — the only object that the rest of
             `src/edge_equation/` should import. Hides the optional ML
             stack behind a clean façade so the deterministic core
             never imports xgboost/shap directly.
"""

from .engine_bridge import NRFIEngineBridge, NRFIBridgeOutput
from .shrinkage import tango_shrink, top_of_order_shrink
from .grading import grade_for_blended

__all__ = [
    "NRFIEngineBridge",
    "NRFIBridgeOutput",
    "tango_shrink",
    "top_of_order_shrink",
    "grade_for_blended",
]
