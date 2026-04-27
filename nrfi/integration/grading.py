"""Map NRFI engine outputs into the deterministic engine's grade /
realization-bucket conventions.

The downstream `engine.betting_engine` expects each `Pick` to carry a
letter grade (A+/A/B/C/D/F) and a `realization` int (47-85, the
calibrated hit-rate bucket). The grade is computed from `edge`; the
realization is then a function of the grade. Both come from
`edge_equation.math.scoring.ConfidenceScorer`.

We also apply a confidence penalty when the NRFI prediction was made
with low sample size (very early-season pitchers) — capped at C grade
to mirror the existing "games_used < 10" rule in `betting_engine.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class GradeOutput:
    grade: str           # "A+", "A", ...
    realization: int     # 47-85 (calibrated bucket)
    edge: Decimal        # Decimal-typed for Pick compatibility


_GRADE_CAP_ON_LOW_SAMPLE = "C"


def grade_for_blended(
    blended_p: float,
    market_implied_p: float,
    *,
    pitcher_batters_faced: float = 0.0,
    low_sample_threshold_bf: float = 120.0,
) -> GradeOutput:
    """Compute the engine grade + realization for a NRFI pick.

    Parameters
    ----------
    blended_p : Calibrated NRFI probability from the engine, [0,1].
    market_implied_p : Market-implied prob (already vig-adjusted).
    pitcher_batters_faced : Sample size on which our pitcher inputs
        rest. Below `low_sample_threshold_bf` we cap the grade at C —
        no pick should claim A+ confidence on three appearances of data.
    """
    from edge_equation.math.scoring import ConfidenceScorer

    edge_dec = Decimal(str(float(blended_p) - float(market_implied_p)))
    # ConfidenceScorer.grade returns a letter; we cap when sample is thin.
    grade = ConfidenceScorer.grade(edge_dec)
    if pitcher_batters_faced < low_sample_threshold_bf:
        grade = _cap_grade(grade, _GRADE_CAP_ON_LOW_SAMPLE)
    realization = ConfidenceScorer.realization_for_grade(grade)
    return GradeOutput(grade=grade, realization=int(realization), edge=edge_dec)


_LETTER_ORDER = ["A+", "A", "B", "C", "D", "F"]


def _cap_grade(actual: str, cap: str) -> str:
    """Return the *worse* (i.e. lower-confidence) of `actual` and `cap`."""
    try:
        i_actual = _LETTER_ORDER.index(actual)
        i_cap = _LETTER_ORDER.index(cap)
    except ValueError:
        return cap
    return _LETTER_ORDER[max(i_actual, i_cap)]
