"""
Grade Track Record — per-(sport, grade) hit rate receipts for the
premium email.

Brand philosophy: Facts Not Feelings. The "A+" label on a pick is only
as meaningful as the base rate behind it. Premium subscribers see the
engine's actual historical hit rate for the grade they're reading,
segmented by sport, so every projection carries its own track record.

Output shape:

    === GRADE TRACK RECORD ===
    MLB  A+ 47-19-2 (71.2%) n=68  ·  A 31-24-0 (56.4%) n=55
    NFL  A  12-6-1  (66.7%) n=19

Settled pick convention (from engine/realization.py):
    realization = 100 -> Win
    realization =  50 -> Push (excluded from both numerator and denom)
    realization =   0 -> Loss
    realization =  -1 -> Void (excluded entirely)

Hit rate: wins / (wins + losses); pushes omitted on both sides.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple
import sqlite3


# Grades the premium track record shows. Renders each engine letter
# as itself -- the prior "B" -> "A-" brand relabel made B-tier picks
# read as a separate "A-" tier in the email and confused readers
# (Apr 26 feedback). The engine grading scale is A+/A/B/C/D/F per
# math/scoring.py; the email now matches that.
_TRACK_GRADES = ("A+", "A", "B")
_GRADE_DISPLAY = {"A+": "A+", "A": "A", "B": "B"}


@dataclass(frozen=True)
class GradeRecord:
    """Historical performance of one (sport, grade) bucket."""
    sport: str
    grade: str              # engine grade (A+/A/B)
    wins: int
    losses: int
    pushes: int
    n_settled: int          # wins + losses + pushes

    @property
    def hit_rate(self) -> Optional[Decimal]:
        denom = self.wins + self.losses
        if denom <= 0:
            return None
        return (Decimal(self.wins) / Decimal(denom)).quantize(Decimal("0.0001"))

    def to_dict(self) -> dict:
        hr = self.hit_rate
        return {
            "sport": self.sport,
            "grade": self.grade,
            "wins": self.wins,
            "losses": self.losses,
            "pushes": self.pushes,
            "n_settled": self.n_settled,
            "hit_rate": str(hr) if hr is not None else None,
        }


def compute_track_record(
    conn: sqlite3.Connection,
    sports: Optional[Sequence[str]] = None,
    grades: Sequence[str] = _TRACK_GRADES,
    min_n: int = 1,
) -> List[GradeRecord]:
    """
    Pull every settled pick and bucket by (sport, grade). Returns only
    buckets with at least `min_n` settled picks so we don't display
    "0-0-0" rows that would erode credibility.

    If `sports` is None we derive the set from whatever sports appear
    in the picks table -- handy for "only show sports we've ever had
    graded on" defaults.
    """
    rows = conn.execute(
        """
        SELECT sport, grade,
               COUNT(*) AS n,
               SUM(CASE WHEN realization = 100 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN realization =   0 THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN realization =  50 THEN 1 ELSE 0 END) AS pushes
          FROM picks
         WHERE realization IN (0, 50, 100)
         GROUP BY sport, grade
        """
    ).fetchall()

    want_sports = set(sports) if sports is not None else None
    want_grades = set(grades)

    out: List[GradeRecord] = []
    for r in rows:
        sport = r["sport"]
        grade = r["grade"]
        if want_sports is not None and sport not in want_sports:
            continue
        if grade not in want_grades:
            continue
        wins = int(r["wins"] or 0)
        losses = int(r["losses"] or 0)
        pushes = int(r["pushes"] or 0)
        n_settled = wins + losses + pushes
        if n_settled < min_n:
            continue
        out.append(GradeRecord(
            sport=sport, grade=grade,
            wins=wins, losses=losses, pushes=pushes,
            n_settled=n_settled,
        ))
    return out


def _cell(rec: GradeRecord) -> str:
    """Render one (sport, grade) cell. hit_rate absent -> "--" so the
    row stays shaped even on brand-new buckets."""
    display_grade = _GRADE_DISPLAY.get(rec.grade, rec.grade)
    hr = rec.hit_rate
    hr_str = f"{(hr * Decimal('100')).quantize(Decimal('0.1'))}%" if hr is not None else "--"
    return (
        f"{display_grade} {rec.wins}-{rec.losses}-{rec.pushes} "
        f"({hr_str}) n={rec.n_settled}"
    )


def format_track_record(records: Sequence[GradeRecord]) -> str:
    """Plain-text receipts block. Groups rows by sport, orders grades
    A+ -> A -> B within each sport. Empty input returns "" so the
    caller can skip the section on a cold DB."""
    if not records:
        return ""
    by_sport: Dict[str, Dict[str, GradeRecord]] = {}
    for r in records:
        by_sport.setdefault(r.sport, {})[r.grade] = r
    out: List[str] = []
    out.append("=== GRADE TRACK RECORD ===")
    for sport in sorted(by_sport.keys()):
        grade_map = by_sport[sport]
        cells: List[str] = []
        for g in _TRACK_GRADES:
            if g in grade_map:
                cells.append(_cell(grade_map[g]))
        if cells:
            out.append(f"  {sport:<4}  " + "  ·  ".join(cells))
    return "\n".join(out) + "\n"
