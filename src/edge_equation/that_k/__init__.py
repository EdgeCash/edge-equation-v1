"""
That K Report -- Pitcher Strikeout Projections.

Separate, focused module for the @ThatK_Guy content brand, kept
INTENTIONALLY out of the main engine so the core daily flow stays
unchanged while we iterate on K-specific signals.

Package layout:

    model.py       -- PitcherProfile / OpponentLineup / GameContext
                      dataclasses + the multiplicative adjustment math
                      that turns a baseline K/BF into a projection.
    simulator.py   -- 5k+ Monte Carlo over negative-binomial draws that
                      turns (mean, dispersion) into a full distribution:
                      mean, stdev, p10/p50/p90, prob_over, prob_under.
    report.py      -- Plain-text renderer matching the exact required
                      "That K Report" output. No hype, no tout lines.
    runner.py      -- Orchestrator: slate rows in, projections out.
    sample_slate.py-- Deterministic dry-run slate so the CLI has
                      something to render without any live data source.
    __main__.py    -- CLI entry: `python -m edge_equation.that_k
                      --sample` or with an explicit CSV/JSON slate.

Design principles:
  * Facts Not Feelings. Every Read line quotes a measured number.
  * No "take the over" or "smash it" language anywhere.
  * Reuses ConfidenceScorer for grade letters so /ThatK grading is
    calibration-consistent with the rest of Edge Equation.
  * Pure stdlib + Decimal + math. No numpy / pandas dependencies
    pulled into the side project.
"""

__all__ = [
    "PitcherProfile",
    "OpponentLineup",
    "GameContext",
    "KProjection",
    "KResult",
    "Ledger",
    "SupportingPost",
    "project_strikeouts",
    "simulate_strikeouts",
    "render_report",
    "render_results_card",
    "render_supporting",
    "build_projections",
    "build_results",
    "generate_supporting",
    "select_types_for_day",
]

from edge_equation.that_k.model import (  # noqa: F401
    GameContext,
    OpponentLineup,
    PitcherProfile,
    project_strikeouts,
)
from edge_equation.that_k.simulator import (  # noqa: F401
    KProjection,
    simulate_strikeouts,
)
from edge_equation.that_k.report import render_report  # noqa: F401
from edge_equation.that_k.runner import build_projections  # noqa: F401
from edge_equation.that_k.results import (  # noqa: F401
    KResult,
    build_results,
    render_results_card,
)
from edge_equation.that_k.ledger import Ledger  # noqa: F401
from edge_equation.that_k.supporting import (  # noqa: F401
    SupportingPost,
    generate_supporting,
    render_supporting,
    select_types_for_day,
)
from edge_equation.that_k.config import (  # noqa: F401
    TargetAccount,
    XCredentials,
    resolve_x_credentials,
    target_header_tag,
)
from edge_equation.that_k.commentary import (  # noqa: F401
    render_day_commentary,
    render_season_commentary,
)
from edge_equation.that_k.clips import (  # noqa: F401
    CLIP_TAG,
    clip_for_k_of_the_night,
    clip_for_throwback,
    render_clip_suggestion,
)
