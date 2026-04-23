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

    `line` is the book's K line. `actual` is the measured strikeout
    count in the start. `label` is the derived Hit / Miss / Push
    tag the renderer prints; callers normally let this module
    compute it via `from_row`."""
    pitcher: str
    line: float
    actual: int
    verdict: str

    @staticmethod
    def from_row(pitcher: str, line: float, actual: int) -> "KResult":
        return KResult(
            pitcher=pitcher.strip(),
            line=float(line),
            actual=int(actual),
            verdict=verdict_for_line(int(actual), float(line)),
        )

    def label(self) -> str:
        return {
            VERDICT_HIT: "Hit",
            VERDICT_MISS: "Miss",
            VERDICT_PUSH: "Push",
        }[self.verdict]

    def to_dict(self) -> dict:
        return {
            "pitcher": self.pitcher,
            "line": self.line,
            "actual": self.actual,
            "verdict": self.verdict,
            "label": self.label(),
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
) -> str:
    """Render the Results card.  When `ledger` is supplied AND
    `update_ledger=True` the snapshot is incremented (idempotently)
    by every verdict before the footer line is composed.

    The renderer never reaches for a default Ledger -- it stays a
    pure function when the caller doesn't pass one, so unit tests
    don't need a temp file.
    """
    out: List[str] = []
    out.append(f"That K Report — Results · {date_str}")
    if intro_70s:
        out.append("Groovy K recap from last night—")
    out.append("Yesterday's K Projections")
    out.append("")

    if not results:
        out.append(
            "  (no settled K projections for this date -- check back "
            "tomorrow once the slate closes out)"
        )
    else:
        for r in results:
            out.append(
                f"• {r.pitcher} {_line_str(r.line)} → {r.actual} K "
                f"({r.label()})"
            )

    out.append("")

    # Season ledger footer. Derived from the passed Ledger if present,
    # otherwise from THIS card's rows alone (useful for dry runs that
    # don't want to touch any file).
    if ledger is not None:
        ledger.load()
        if update_ledger and results:
            ledger.record_many(
                date=date_str,
                rows=((r.pitcher, _line_str(r.line), r.verdict) for r in results),
            )
            ledger.flush()
        snap = ledger.summary()
        ledger_wins = snap.wins
        ledger_losses = snap.losses
        ledger_pushes = snap.pushes
    else:
        ledger_wins = sum(1 for r in results if r.verdict == VERDICT_HIT)
        ledger_losses = sum(1 for r in results if r.verdict == VERDICT_MISS)
        ledger_pushes = sum(1 for r in results if r.verdict == VERDICT_PUSH)

    total_graded = ledger_wins + ledger_losses
    out.append("Season Ledger (K Props)")
    if total_graded == 0:
        out.append("  (tracking -- first settled result will land here)")
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
    {"pitcher": "...", "line": 7.5, "actual": 9}. Keeps callers from
    having to construct dataclasses by hand."""
    out: List[KResult] = []
    for r in rows:
        out.append(
            KResult.from_row(
                pitcher=r["pitcher"],
                line=r["line"],
                actual=r["actual"],
            )
        )
    return out


def load_ledger(path: Optional[Path] = None) -> Ledger:
    """Small convenience so the CLI can pass through a --ledger flag
    without repeating the path dance."""
    return Ledger(path or DEFAULT_LEDGER_PATH)
