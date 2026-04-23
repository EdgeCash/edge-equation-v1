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

from edge_equation.that_k.config import TargetAccount, target_header_tag
from edge_equation.that_k.grading import grade_k_edge, grade_rank, is_top_play
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
# Back-compat default for legacy --top-n usage; new two-section
# output uses TOP_PLAYS_MIN/MAX below.
DEFAULT_TOP_N = 8
# Top Plays section size per the final-pass brief: "3-6 entries".
TOP_PLAYS_MIN = 3
TOP_PLAYS_MAX = 6


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
    """Translate the MC probability-edge into a K-prop grade.

    Phase: switched from ConfidenceScorer to the K-specific ladder in
    grading.py so Top Plays surface at the correct tier.  Probability-
    space edge is the natural K-prop signal: |prob_over - 0.5|, so
    over-leans and under-leans are graded identically.
    """
    return grade_k_edge(projection.edge_prob)


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


# Optional 70s intro line. Pulled from a small rotation keyed to the
# run date so repeat opens never feel stale.  STRICTLY intro-only --
# the analytical body of every row stays clean per brand rules.
_INTRO_70S_ROTATION = (
    "Groovy K Report for tonight—",
    "Right on, tonight's K read—",
    "Far out K slate locked in—",
    "Keep on whiffin'—tonight's K projections—",
    "Tonight's K slate, clean and factual—",
)


def _intro_for(date_str: str) -> str:
    """Pick a deterministic intro for `date_str` from the rotation.
    No RNG: the ordinal-date modulo the rotation length indexes
    directly so the same date always produces the same intro."""
    import datetime as _dt
    try:
        base = _dt.date.fromisoformat(date_str).toordinal()
    except (TypeError, ValueError):
        base = abs(hash(date_str))
    return _INTRO_70S_ROTATION[base % len(_INTRO_70S_ROTATION)]


def render_report(
    rows: Iterable[KReportRow],
    date_str: str,
    top_n: Optional[int] = DEFAULT_TOP_N,
    intro_70s: bool = False,
    target_account: TargetAccount = TargetAccount.KGUY,
    *,
    full_slate: bool = True,
    top_plays_max: int = TOP_PLAYS_MAX,
    top_plays_min: int = TOP_PLAYS_MIN,
) -> str:
    """Render the full text report.

    Two-section layout per the final-pass brief:

        That K Report — 2026-04-23
          (target=@ThatK_Guy)
        Tonight's Top Plays (A- and higher)
        • Paul Skenes (PIT) vs. HOU  Line: 7.5  K Projection: 9.2
          K Grade: A+  Edge: ...
        ...

        Full Slate Projections
        • Gerrit Cole (NYY) vs. BOS  Line: 7.5  ...
        ...

        Powered by Edge Equation

    Top Plays = rows graded A- or higher (K-specific grader).  The
    section caps at `top_plays_max` (default 6).  When fewer than
    `top_plays_min` rows qualify, the Top Plays section renders an
    explicit empty-state line rather than get skipped silently --
    subscribers need to see the slot exists.

    full_slate=False collapses the output to just the Top Plays
    section -- useful for short-form X posts where the full list
    would exceed the character cap.
    """
    rows = list(rows)
    # Rank by (grade_rank desc, edge_prob desc, |edge_ks| desc) so
    # two rows with the same grade still order deterministically.
    rows.sort(
        key=lambda r: (
            grade_rank(r.grade),
            r.projection.edge_prob,
            abs(r.projection.edge_ks),
        ),
        reverse=True,
    )
    # Back-compat: legacy top_n applies only to the Full Slate
    # section so callers that passed --top-n 8 still see a capped
    # output.  Top Plays has its own explicit cap.
    full_slate_rows = rows if top_n in (None, 0) else rows[:top_n]
    top_plays = [r for r in rows if is_top_play(r.grade)][:top_plays_max]

    out: List[str] = []
    # Brief specifies an em-dash ("That K Report — [Date]") so we match
    # verbatim.  Em-dash renders cleanly in all modern mail/X clients.
    out.append(f"That K Report — {date_str}")
    out.append(f"  ({target_header_tag(target_account)})")
    if intro_70s:
        out.append(_intro_for(date_str))
    out.append("")

    # ----- Tonight's Top Plays (A- and higher) ------------------------
    out.append("Tonight's Top Plays (A- and higher)")
    if len(top_plays) < top_plays_min:
        out.append(
            f"  (no edges cleared the A- threshold tonight -- {len(top_plays)} "
            f"of {len(rows)} starters qualified)"
        )
    else:
        for i, row in enumerate(top_plays):
            if i > 0:
                out.append("")
            out.extend(render_row(row))
    out.append("")

    # ----- Full Slate Projections -------------------------------------
    if full_slate:
        out.append("Full Slate Projections")
        if not full_slate_rows:
            out.append("  (no probable starters on tonight's board)")
        else:
            for i, row in enumerate(full_slate_rows):
                if i > 0:
                    out.append("")
                out.extend(render_row(row))
        out.append("")

    out.append(BRAND_FOOTER)
    return "\n".join(out).rstrip() + "\n"
