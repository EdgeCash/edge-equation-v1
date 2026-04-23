"""
That K Report -- Results card + ledger integration.

Renders yesterday's closed-out K projections in the brand's exact
"Results" format and (when a Ledger path is passed) updates the
season totals so the footer line always reflects the latest state:

    That K Report — Results
    Yesterday's K Projections

    • Gerrit Cole 7.5 → 9 K (Hit)
    • Tarik Skubal 8.5 → 6 K (Miss)
    ...

    Season Ledger (K Props)
    42-29 (59% hit rate)

    Powered by Edge Equation

Design rules:
  * Every verdict comes from verdict_for_line(actual, line) so a
    whole-number line pushes instead of silently counting as a hit.
  * Ledger writes are dedup'd on (date, pitcher, line) so reruns
    of the same day don't double-count the season totals.
  * No tout language, no "shoulda / coulda" rhetoric. Just the
    facts with a Hit / Miss / Push marker.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from edge_equation.that_k.calibration import (
    CalibrationSnapshot,
    SettledRow,
    build_settled_rows,
    compute_calibration,
)
from edge_equation.that_k.commentary import (
    render_day_commentary,
    render_season_commentary,
)
from edge_equation.that_k.config import (
    TargetAccount,
    target_header_tag,
)
from edge_equation.that_k.grading import is_top_play as _grade_is_top_play
from edge_equation.that_k.ledger import (
    DEFAULT_LEDGER_PATH,
    Ledger,
    VERDICT_HIT,
    VERDICT_MISS,
    VERDICT_PUSH,
    verdict_for_line,
)


BRAND_FOOTER = "Powered by Edge Equation"


@dataclass(frozen=True)
class KResult:
    """One pitcher's settled K outcome from the previous slate.

    `line` is the book's K line.  `actual` is the measured strikeout
    count in the start.  `verdict` is the derived Hit / Miss / Push
    tag.  `grade` + `projected_mean` are optional but preferred -- when
    present the Results card can compute MAE + separate Top Plays W-L
    from the full-slate calibration block.
    """
    pitcher: str
    line: float
    actual: int
    verdict: str
    grade: Optional[str] = None
    projected_mean: Optional[float] = None

    @staticmethod
    def from_row(
        pitcher: str, line: float, actual: int,
        grade: Optional[str] = None,
        projected_mean: Optional[float] = None,
    ) -> "KResult":
        return KResult(
            pitcher=pitcher.strip(),
            line=float(line),
            actual=int(actual),
            verdict=verdict_for_line(int(actual), float(line)),
            grade=(grade or None),
            projected_mean=(
                float(projected_mean) if projected_mean is not None else None
            ),
        )

    def label(self) -> str:
        return {
            VERDICT_HIT: "Hit",
            VERDICT_MISS: "Miss",
            VERDICT_PUSH: "Push",
        }[self.verdict]

    def is_top_play(self) -> bool:
        return _grade_is_top_play(self.grade or "")

    def to_dict(self) -> dict:
        return {
            "pitcher": self.pitcher,
            "line": self.line,
            "actual": self.actual,
            "verdict": self.verdict,
            "label": self.label(),
            "grade": self.grade,
            "projected_mean": self.projected_mean,
            "is_top_play": self.is_top_play(),
        }


def _line_str(line: float) -> str:
    """Whole-number lines render as '5', half lines as '7.5'."""
    d = Decimal(str(line)).normalize()
    s = str(d)
    if "." not in s:
        return s
    if s.endswith("0"):
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def render_results_card(
    results: Sequence[KResult],
    date_str: str,
    ledger: Optional[Ledger] = None,
    *,
    intro_70s: bool = False,
    update_ledger: bool = True,
    commentary: bool = True,
    target_account: TargetAccount = TargetAccount.KGUY,
) -> str:
    """Render the Results card per the final-pass three-section brief:

        That K Report — Results · 2026-04-22
          (target=@ThatK_Guy)

        Yesterday's Top Plays (A- and higher)
          2-1 (67%) -- Far out, man -- today went 2-1 (67% hit rate).

        Full Slate Calibration
          All projections: 5-3 on the line (62%) | Average error: 1.6 K

        Season Ledger (A- and higher only)
          42-29 (59% hit rate)

        Powered by Edge Equation

    Top Plays and Full Slate tracks are driven by the K-specific
    grade ladder (grading.is_top_play) using each result row's `grade`
    field.  When a row omits its grade the result still counts toward
    Full Slate calibration but is EXCLUDED from Top Plays -- matches
    the brand rule "Main ledger = A- and higher only".
    """
    # Pre-split into Top Plays vs Full Slate so both sections render
    # from the same underlying verdicts.
    top_rows = [r for r in results if r.is_top_play()]
    all_rows = list(results)

    day_snap = compute_calibration([
        SettledRow(
            pitcher=r.pitcher, line=r.line, actual=r.actual,
            projected_mean=r.projected_mean, grade=r.grade,
        )
        for r in all_rows
    ])

    out: List[str] = []
    out.append(f"That K Report — Results · {date_str}")
    out.append(f"  ({target_header_tag(target_account)})")
    if intro_70s:
        out.append("Groovy K recap from last night—")
    out.append("")

    # ----- Yesterday's Top Plays (A- and higher) --------------------
    out.append("Yesterday's Top Plays (A- and higher)")
    if not top_rows:
        out.append(
            "  (no Top Plays settled for this date -- check back once "
            "yesterday's slate closes out)"
        )
    else:
        tp_w = day_snap.top_plays_wins
        tp_l = day_snap.top_plays_losses
        tp_p = day_snap.top_plays_pushes
        tp_graded = tp_w + tp_l
        rate_part = (
            f"{tp_w / tp_graded * 100:.0f}%" if tp_graded else "--"
        )
        push_part = f" · {tp_p} push" if tp_p else ""
        commentary_line = ""
        if commentary and tp_graded:
            c = render_day_commentary(
                wins=tp_w, losses=tp_l, pushes=tp_p,
                seed_key=f"{date_str}:top",
            )
            if c is not None:
                commentary_line = f" -- {c.phrase}"
        out.append(
            f"  {tp_w}-{tp_l} ({rate_part}){push_part}{commentary_line}"
        )
        # Per-row Hit/Miss roster so the reader sees the actual rows
        # behind the tally.  Follows the rollup line, indented.
        for r in top_rows:
            out.append(
                f"  • {r.pitcher} {_line_str(r.line)} → {r.actual} K "
                f"({r.label()})"
            )
    out.append("")

    # ----- Full Slate Calibration -----------------------------------
    out.append("Full Slate Calibration")
    if not all_rows:
        out.append(
            "  (no settled K projections for this date)"
        )
    else:
        fw = day_snap.full_wins
        fl = day_snap.full_losses
        fp = day_snap.full_pushes
        graded = fw + fl
        rate_part = f"{fw / graded * 100:.0f}%" if graded else "--"
        mae_part = (
            f" | Average error: {day_snap.mae_ks:.1f} K"
            if day_snap.mae_ks is not None else ""
        )
        push_part = f" · {fp} push" if fp else ""
        out.append(
            f"  All projections: {fw}-{fl} on the line "
            f"({rate_part}){push_part}{mae_part}"
        )
    out.append("")

    # ----- Season Ledger (A- and higher only) -----------------------
    if ledger is not None:
        ledger.load()
        if update_ledger and all_rows:
            ledger.record_many(
                date=date_str,
                rows=(
                    (r.pitcher, _line_str(r.line), r.verdict, r.is_top_play())
                    for r in all_rows
                ),
            )
            ledger.flush()
        snap = ledger.summary()
        ledger_wins = snap.wins
        ledger_losses = snap.losses
        ledger_pushes = snap.pushes
    else:
        ledger_wins = day_snap.top_plays_wins
        ledger_losses = day_snap.top_plays_losses
        ledger_pushes = day_snap.top_plays_pushes

    total_graded = ledger_wins + ledger_losses
    out.append("Season Ledger (A- and higher only)")
    if total_graded == 0:
        out.append("  (tracking -- first Top Play will land here)")
    else:
        hit_rate = ledger_wins / total_graded
        ledger_line = f"  {ledger_wins}-{ledger_losses} ({hit_rate*100:.0f}% hit rate)"
        if ledger_pushes:
            ledger_line += f" · {ledger_pushes} push"
        out.append(ledger_line)

    out.append("")
    out.append(BRAND_FOOTER)
    return "\n".join(out).rstrip() + "\n"


def build_results(rows: Iterable[dict]) -> List[KResult]:
    """Accept the same row-dict shape the slate uses -- each row is
    {"pitcher": "...", "line": 7.5, "actual": 9, "grade": "A-",
     "projected_mean": 8.2}.  Grade + projected_mean are optional --
    Results rows ship the minimum; the projections pipeline writes
    the richer metrics flavor with grade + projected_mean for
    Full Slate calibration."""
    out: List[KResult] = []
    for r in rows:
        out.append(
            KResult.from_row(
                pitcher=r["pitcher"],
                line=r["line"],
                actual=r["actual"],
                grade=r.get("grade"),
                projected_mean=r.get("projected_mean"),
            )
        )
    return out


def load_ledger(path: Optional[Path] = None) -> Ledger:
    """Small convenience so the CLI can pass through a --ledger flag
    without repeating the path dance."""
    return Ledger(path or DEFAULT_LEDGER_PATH)
