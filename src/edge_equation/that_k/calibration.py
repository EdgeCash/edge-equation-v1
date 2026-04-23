"""
That K Report -- calibration metrics.

Pure functions that turn a settled slate into the aggregates the
Results card and the metrics debug JSON both consume:

  * Top Plays W-L (A- and higher only) -- drives the Season Ledger.
  * Full Slate W-L (every probable SP that shipped a projection) --
    drives the calibration line.
  * MAE / RMSE between projected K mean and actual K count -- drives
    the "Average error: X.X K" line on the Results card.

Everything here is stdlib only so the Results path stays fast and
side-effect-free.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List, Optional, Sequence

from edge_equation.that_k.grading import is_top_play
from edge_equation.that_k.ledger import (
    VERDICT_HIT,
    VERDICT_MISS,
    VERDICT_PUSH,
    verdict_for_line,
)


@dataclass(frozen=True)
class SettledRow:
    """One settled K outcome, joined with its original projection.

    Supplying `projected_mean` + `grade` lets the calibration module
    compute both the W-L tally AND the MAE tier breakdown without
    forcing the caller to maintain two separate data structures.
    """
    pitcher: str
    line: float
    actual: int
    projected_mean: Optional[float] = None
    grade: Optional[str] = None

    @property
    def verdict(self) -> str:
        return verdict_for_line(int(self.actual), float(self.line))

    def to_dict(self) -> dict:
        return {
            "pitcher": self.pitcher,
            "line": self.line,
            "actual": self.actual,
            "projected_mean": self.projected_mean,
            "grade": self.grade,
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class CalibrationSnapshot:
    """Aggregated metrics for a single slate."""
    top_plays_wins: int
    top_plays_losses: int
    top_plays_pushes: int
    full_wins: int
    full_losses: int
    full_pushes: int
    mae_ks: Optional[float]        # mean absolute error vs projection
    rmse_ks: Optional[float]       # root-mean-square error
    n_projections: int             # rows with projected_mean present
    avg_edge_hit_rate: Optional[float] = None  # reserved

    def top_plays_hit_rate(self) -> Optional[float]:
        n = self.top_plays_wins + self.top_plays_losses
        if n == 0:
            return None
        return self.top_plays_wins / n

    def full_slate_hit_rate(self) -> Optional[float]:
        n = self.full_wins + self.full_losses
        if n == 0:
            return None
        return self.full_wins / n

    def to_dict(self) -> dict:
        return {
            "top_plays_wins": self.top_plays_wins,
            "top_plays_losses": self.top_plays_losses,
            "top_plays_pushes": self.top_plays_pushes,
            "top_plays_hit_rate": self.top_plays_hit_rate(),
            "full_wins": self.full_wins,
            "full_losses": self.full_losses,
            "full_pushes": self.full_pushes,
            "full_slate_hit_rate": self.full_slate_hit_rate(),
            "mae_ks": self.mae_ks,
            "rmse_ks": self.rmse_ks,
            "n_projections": self.n_projections,
        }


def compute_calibration(rows: Sequence[SettledRow]) -> CalibrationSnapshot:
    """Walk a settled slate and return the aggregate snapshot.  Empty
    input returns zeros + None for MAE/RMSE so the renderer can show
    an empty-state line without special casing."""
    tp_w = tp_l = tp_p = 0
    fs_w = fs_l = fs_p = 0
    abs_errors: List[float] = []
    sq_errors: List[float] = []
    n_proj = 0

    for r in rows:
        v = r.verdict
        # Full slate tally.
        if v == VERDICT_HIT:
            fs_w += 1
        elif v == VERDICT_MISS:
            fs_l += 1
        else:
            fs_p += 1
        # Top Plays tally -- requires a grade at A- or higher.
        if is_top_play(r.grade or ""):
            if v == VERDICT_HIT:
                tp_w += 1
            elif v == VERDICT_MISS:
                tp_l += 1
            else:
                tp_p += 1
        # Error buckets: only counted when we have a numeric
        # projection to compare against. Skips bare "results-only"
        # rows that have no engine projection attached.
        if r.projected_mean is not None:
            n_proj += 1
            err = float(r.actual) - float(r.projected_mean)
            abs_errors.append(abs(err))
            sq_errors.append(err * err)

    mae = sum(abs_errors) / len(abs_errors) if abs_errors else None
    rmse = math.sqrt(sum(sq_errors) / len(sq_errors)) if sq_errors else None

    return CalibrationSnapshot(
        top_plays_wins=tp_w,
        top_plays_losses=tp_l,
        top_plays_pushes=tp_p,
        full_wins=fs_w,
        full_losses=fs_l,
        full_pushes=fs_p,
        mae_ks=mae,
        rmse_ks=rmse,
        n_projections=n_proj,
    )


def build_settled_rows(rows: Iterable[dict]) -> List[SettledRow]:
    """Dict -> SettledRow helper. Accepts the same row shape the
    Results CLI takes, plus optional `projected_mean` + `grade` keys
    that the projections pipeline writes into its metrics artifact."""
    out: List[SettledRow] = []
    for r in rows:
        out.append(
            SettledRow(
                pitcher=str(r["pitcher"]).strip(),
                line=float(r["line"]),
                actual=int(r["actual"]),
                projected_mean=(
                    float(r["projected_mean"])
                    if r.get("projected_mean") is not None else None
                ),
                grade=(r.get("grade") or None),
            )
        )
    return out
