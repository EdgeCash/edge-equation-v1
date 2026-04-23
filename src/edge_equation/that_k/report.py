"""
That K Report -- plain-text renderer.

Output format (exact -- the @ThatK_Guy account posts this verbatim):

    That K Report -- 2026-04-23
    Tonight's Pitcher K Projections

    • Gerrit Cole (NYY) vs. BOS
      Line: 7.5
      K Projection: 8.2
      Grade: A
      Edge: Red Sox lineup runs a 12.4% SwStr vs RHP, HP ump +K 1.06x,
            Cole's last three starts at 1.15 K/BF.

    (repeat per qualifying starter or top 8 on the slate)

    Powered by Edge Equation

Rendering rules:
  * Indentation is exactly two spaces per sub-bullet.
  * One blank line between pitcher blocks.
  * "Edge:" line is a factual one-sentence read citing 1-3 real
    factors.  No tout language ("smash it", "cash", "lock"), no
    imperative recommendations ("take the over").  If the numbers
    don't support anything specific, the line stays short and
    quotes the MC band.
  * Grades come from ConfidenceScorer keyed to the probability-space
    edge (|prob_over - 0.5|) so the @ThatK_Guy brand uses the same
    grading ladder as the rest of Edge Equation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List, Optional

from edge_equation.math.scoring import ConfidenceScorer
from edge_equation.that_k.model import (
    GameContext,
    KProjectionInputs,
    LEAGUE_CSW,
    LEAGUE_SWSTR,
    LEAGUE_UMP_K_FACTOR,
    OpponentLineup,
    PitcherProfile,
)
from edge_equation.that_k.simulator import KProjection


BRAND_HEADER = "Tonight's Pitcher K Projections"
BRAND_FOOTER = "Powered by Edge Equation"
# Default slate cap matches the brief: "every qualifying starter or
# top 8".  Callers can override; the CLI surfaces --top-n.
DEFAULT_TOP_N = 8


@dataclass(frozen=True)
class KReportRow:
    """One starter's projection + rendered Read context."""
    projection: KProjection
    inputs: KProjectionInputs
    pitcher: PitcherProfile
    lineup: OpponentLineup
    context: GameContext
    grade: str

    def to_dict(self) -> dict:
        return {
            "projection": self.projection.to_dict(),
            "inputs": self.inputs.to_dict(),
            "pitcher": self.pitcher.to_dict(),
            "lineup": self.lineup.to_dict(),
            "context": self.context.to_dict(),
            "grade": self.grade,
        }


def grade_row(projection: KProjection) -> str:
    """Translate the MC probability-edge into an A+/A/B/C/D/F grade.

    Same ConfidenceScorer thresholds the main engine uses so the
    brand stays calibration-consistent.  Probability-space edge is
    the natural K-prop signal: prob_over - 0.5 (signed), magnitude
    consumed here so under-leans and over-leans are graded identically.
    """
    return ConfidenceScorer.grade(projection.edge_prob)


def _edge_read(row: KReportRow) -> str:
    """Compose the 1-sentence factual Read line.  Facts Not Feelings."""
    p = row.projection
    inp = row.inputs
    lineup = row.lineup
    pitcher = row.pitcher
    ctx = row.context

    factors: List[str] = []

    # Lineup SwStr / CSW -- lead with the strongest.
    swstr_shown = lineup.swstr_pct
    if pitcher.throws == "L" and lineup.swstr_vs_L is not None:
        swstr_shown = lineup.swstr_vs_L
    elif pitcher.throws == "R" and lineup.swstr_vs_R is not None:
        swstr_shown = lineup.swstr_vs_R
    if abs(swstr_shown - LEAGUE_SWSTR) >= 0.005:
        factors.append(
            f"{lineup.team} lineup {swstr_shown*100:.1f}% SwStr "
            f"vs {pitcher.throws}HP"
        )
    elif abs(lineup.csw_pct - LEAGUE_CSW) >= 0.005:
        factors.append(f"{lineup.team} {lineup.csw_pct*100:.1f}% CSW")

    # Umpire when non-neutral.
    if abs(inp.umpire_adj - LEAGUE_UMP_K_FACTOR) >= 0.015:
        name = ctx.umpire_name or "HP ump"
        sign = "+K" if inp.umpire_adj > 1.0 else "-K"
        factors.append(f"HP ump {name} {sign} {inp.umpire_adj:.2f}x")

    # Weather (skip if dome or neutral).
    if not ctx.dome and abs(inp.weather_adj - 1.0) >= 0.01:
        if ctx.temp_f is not None:
            factors.append(f"{ctx.temp_f:.0f}°F")
        elif ctx.wind_mph is not None:
            factors.append(f"wind {ctx.wind_mph:.0f} mph")

    # Recent form -- only when it meaningfully moves the needle.
    if abs(inp.form_adj - 1.0) >= 0.015 and pitcher.recent_k_per_bf:
        n_starts = len(pitcher.recent_k_per_bf)
        recent_sum = sum(v for v, _ in pitcher.recent_k_per_bf)
        recent_avg = recent_sum / n_starts if n_starts else 0
        factors.append(
            f"last {n_starts} starts at {recent_avg*100:.1f}% K/BF"
        )

    # Arsenal match-up.
    if abs(inp.arsenal_adj - 1.0) >= 0.015 and pitcher.arsenal:
        top_pitch = max(pitcher.arsenal.items(), key=lambda kv: kv[1])[0]
        factors.append(f"arsenal-vs-{top_pitch} edge")

    # Handedness platoon.
    if abs(inp.handedness_adj - 1.0) >= 0.015:
        side = "same-handed" if inp.handedness_adj > 1.0 else "cross-handed"
        factors.append(f"{side} lineup")

    # Sample-size caveat rides LAST so the main factors lead.
    if inp.sample_warning:
        factors.append("limited recent-start sample")

    # MC fallback when nothing specific moves the needle -- still
    # factual, quotes the distribution directly instead of going vague.
    if not factors:
        return (
            f"MC projection {p.mean} Ks vs line {p.line} "
            f"(p10-p90 {p.p10}-{p.p90})."
        )

    # Cap the Read to the three most substantive factors so it stays
    # one sentence on a single line in the posting tool.
    head = ", ".join(factors[:3])
    return f"{head}."


def _line_str(line: Decimal) -> str:
    """7.5 renders as '7.5', 8 renders as '8'."""
    s = str(line)
    if s.endswith(".0"):
        return s[:-2]
    return s


def render_row(row: KReportRow) -> List[str]:
    """Render one pitcher block per the required output format."""
    p = row.projection
    line = _line_str(p.line)
    mean = _line_str(p.mean)
    lines = [
        f"• {row.pitcher.name} ({p.team}) vs. {p.opponent}",
        f"  Line: {line}",
        f"  K Projection: {mean}",
        f"  Grade: {row.grade}",
        f"  Edge: {_edge_read(row)}",
    ]
    return lines


def render_report(
    rows: Iterable[KReportRow],
    date_str: str,
    top_n: Optional[int] = DEFAULT_TOP_N,
) -> str:
    """Render the full text report.  `date_str` is a YYYY-MM-DD string
    (caller passes run-date; the module doesn't reach for the clock)."""
    rows = list(rows)
    # Rank by probability-space edge magnitude, ties broken by raw K
    # edge magnitude so two equally-graded rows still order
    # deterministically.
    rows.sort(
        key=lambda r: (r.projection.edge_prob, abs(r.projection.edge_ks)),
        reverse=True,
    )
    if top_n is not None and top_n > 0:
        rows = rows[:top_n]

    out: List[str] = []
    # Brief specifies an em-dash ("That K Report — [Date]") so we match
    # verbatim.  Em-dash renders cleanly in all modern mail/X clients.
    out.append(f"That K Report — {date_str}")
    out.append(BRAND_HEADER)
    out.append("")
    for i, row in enumerate(rows):
        if i > 0:
            out.append("")
        out.extend(render_row(row))
    out.append("")
    out.append(BRAND_FOOTER)
    return "\n".join(out).rstrip() + "\n"
