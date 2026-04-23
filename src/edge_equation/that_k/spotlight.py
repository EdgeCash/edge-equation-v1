"""
That K Report -- Weekly Pitcher Spotlight.

Manual long-form feature published once per week on @ThatK_Guy.
Structure matches the brand's spec: arsenal breakdown, movement &
release, edge read, highlight clip, tie-in to that night's
projection.  100% analytical; tight prose, no tout language.

The renderer consumes a plain dict so the caller composes the
spotlight subject in a JSON slate entry (same shape the sample
slate uses, plus a `movement` block).  Nothing here reaches for
live data; side-project discipline -- hook a real feed later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from edge_equation.that_k.clips import render_clip_suggestion
from edge_equation.that_k.config import TargetAccount, target_header_tag


BRAND_FOOTER = "Powered by Edge Equation"


@dataclass(frozen=True)
class SpotlightSubject:
    """Single pitcher focal point for the week.  Every field is
    optional so we degrade cleanly if a section's data is missing."""
    pitcher: str
    team: str
    opponent: Optional[str] = None
    throws: str = "R"
    arsenal: Optional[Dict[str, Dict[str, float]]] = None  # pitch -> {usage_pct, swstr, spin_rpm, velo_mph}
    movement: Optional[Dict[str, Dict[str, float]]] = None  # pitch -> {iv_break_in, hz_break_in, release_pt_ft}
    edge_read: Optional[str] = None
    projection_mean: Optional[float] = None
    projection_line: Optional[float] = None
    projection_grade: Optional[str] = None
    clip: Optional[str] = None      # description (no URL) or search string

    def to_dict(self) -> dict:
        return {
            "pitcher": self.pitcher,
            "team": self.team,
            "opponent": self.opponent,
            "throws": self.throws,
            "arsenal": self.arsenal,
            "movement": self.movement,
            "edge_read": self.edge_read,
            "projection_mean": self.projection_mean,
            "projection_line": self.projection_line,
            "projection_grade": self.projection_grade,
            "clip": self.clip,
        }


def _arsenal_lines(arsenal: Optional[Dict[str, Dict[str, float]]]) -> List[str]:
    """Render the Arsenal Breakdown block.  Rows sort by usage_pct
    descending so the primary pitch leads."""
    if not arsenal:
        return ["  (arsenal data not supplied for this spotlight)"]
    rows = []
    for pitch, stats in arsenal.items():
        rows.append((
            pitch,
            float(stats.get("usage_pct", 0.0)),
            float(stats.get("swstr", 0.0)),
            stats.get("velo_mph"),
            stats.get("spin_rpm"),
        ))
    rows.sort(key=lambda r: r[1], reverse=True)
    out: List[str] = []
    for pitch, usage, swstr, velo, spin in rows:
        bits = [f"{pitch} -- {usage*100:.0f}% usage, {swstr*100:.1f}% SwStr"]
        if velo is not None:
            bits.append(f"{float(velo):.1f} mph")
        if spin is not None:
            bits.append(f"{int(spin)} rpm")
        out.append("  • " + ", ".join(bits))
    return out


def _movement_lines(movement: Optional[Dict[str, Dict[str, float]]]) -> List[str]:
    """Movement & Release block: vertical / horizontal break, release
    height.  Missing data collapses the line; never fabricates a number."""
    if not movement:
        return ["  (movement profile not supplied for this spotlight)"]
    out: List[str] = []
    for pitch, stats in movement.items():
        bits = [pitch]
        iv = stats.get("iv_break_in")
        hz = stats.get("hz_break_in")
        rel = stats.get("release_pt_ft")
        if iv is not None:
            bits.append(f"IV break {float(iv):+.1f}\"")
        if hz is not None:
            bits.append(f"HB {float(hz):+.1f}\"")
        if rel is not None:
            bits.append(f"release {float(rel):.1f} ft")
        if len(bits) == 1:
            continue
        out.append("  • " + ", ".join(bits))
    return out or ["  (no non-default movement fields supplied)"]


def render_spotlight(
    subject: SpotlightSubject,
    week_of: str,
    *,
    target_account: TargetAccount = TargetAccount.KGUY,
) -> str:
    """Render the Weekly Pitcher Spotlight card.  `week_of` is a
    YYYY-MM-DD string (the Monday of the feature week, by
    convention)."""
    out: List[str] = []
    out.append(f"That K Report — Pitcher Spotlight · Week of {week_of}")
    out.append(f"  ({target_header_tag(target_account)})")
    header_bits = [subject.pitcher, f"({subject.team}, {subject.throws}HP)"]
    if subject.opponent:
        header_bits.append(f"vs. {subject.opponent}")
    out.append(" ".join(header_bits))
    out.append("")

    out.append("Arsenal Breakdown")
    out.extend(_arsenal_lines(subject.arsenal))
    out.append("")

    out.append("Movement & Release")
    out.extend(_movement_lines(subject.movement))
    out.append("")

    out.append("Edge Read")
    if subject.edge_read:
        out.append(f"  {subject.edge_read}")
    else:
        out.append(
            "  (edge read not supplied -- populate the spotlight JSON "
            "before publishing)"
        )
    out.append("")

    # Tie-in to tonight's projection when the caller hands us one.
    if subject.projection_mean is not None and subject.projection_line is not None:
        grade_part = (
            f" (Grade {subject.projection_grade})"
            if subject.projection_grade else ""
        )
        out.append("Projection Tie-In")
        out.append(
            f"  Tonight's projection: {subject.projection_mean} K vs line "
            f"{subject.projection_line}{grade_part}."
        )
        out.append("")

    if subject.clip:
        out.append(render_clip_suggestion(subject.clip))
        out.append("")

    out.append(BRAND_FOOTER)
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------- sample fixture

def sample_spotlight() -> SpotlightSubject:
    """Deterministic dry-run Spotlight for the CLI's --sample mode.
    Uses real (public) pitcher mechanics ballpark so reviewers can
    audit the numbers against a familiar reference."""
    return SpotlightSubject(
        pitcher="Paul Skenes",
        team="PIT",
        opponent="HOU",
        throws="R",
        arsenal={
            "FF": {"usage_pct": 0.42, "swstr": 0.115, "velo_mph": 98.4, "spin_rpm": 2380},
            "SL": {"usage_pct": 0.21, "swstr": 0.190, "velo_mph": 87.2, "spin_rpm": 2620},
            "SPL": {"usage_pct": 0.18, "swstr": 0.180, "velo_mph": 90.1, "spin_rpm": 1540},
            "CU": {"usage_pct": 0.12, "swstr": 0.150, "velo_mph": 82.0, "spin_rpm": 2980},
            "CH": {"usage_pct": 0.07, "swstr": 0.120, "velo_mph": 89.6, "spin_rpm": 1720},
        },
        movement={
            "FF": {"iv_break_in": 17.2, "hz_break_in": -6.1, "release_pt_ft": 6.2},
            "SL": {"iv_break_in": -1.5, "hz_break_in": 4.3},
            "SPL": {"iv_break_in": 0.8, "hz_break_in": 8.0},
        },
        edge_read=(
            "Skenes pairs top-decile velocity with an elite slider whiff "
            "rate (19.0%) against a HOU lineup running a 9.8% SwStr vs RHP "
            "(league avg 11.0%). Arsenal-vs-SL edge reads ~ +3% on the MC."
        ),
        projection_mean=9.2,
        projection_line=7.5,
        projection_grade="A+",
        clip="Savant pitch-movement reel for Skenes' 5-pitch arsenal.",
    )
