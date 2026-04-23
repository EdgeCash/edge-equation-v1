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

from edge_equation.that_k.commentary import (
    render_day_commentary,
    render_season_commentary,
)
from edge_equation.that_k.config import (
    TargetAccount,
    target_header_tag,
)
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
    commentary: bool = True,
    target_account: TargetAccount = TargetAccount.KGUY,
) -> str:
    """Render the Results card.  When `ledger` is supplied AND
    `update_ledger=True` the snapshot is incremented (idempotently)
    by every verdict before the footer line is composed.

    commentary=True appends a short hit-rate-bucketed 70s-flair line
    below the Season Ledger block that ties the day's W-L to a bucket
    phrase per the brand's tone rules.  The commentary sits in its
    own section so downstream parsers can strip it without touching
    the factual Hit/Miss rows above.

    target_account stamps an audit-only header tag so artifacts
    carry which identity they were built for (no secret material).

    The renderer never reaches for a default Ledger -- it stays a
    pure function when the caller doesn't pass one, so unit tests
    don't need a temp file.
    """
    out: List[str] = []
    out.append(f"That K Report — Results · {date_str}")
    out.append(f"  ({target_header_tag(target_account)})")
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

    # Day-level commentary block. Ties the light 70s flair to TODAY's
    # actual W-L (not season) so the tone always matches the slate
    # the reader just saw. Falls back silently when the day had
    # nothing settled.
    if commentary and results:
        day_wins = sum(1 for r in results if r.verdict == VERDICT_HIT)
        day_losses = sum(1 for r in results if r.verdict == VERDICT_MISS)
        day_pushes = sum(1 for r in results if r.verdict == VERDICT_PUSH)
        day_comment = render_day_commentary(
            wins=day_wins, losses=day_losses, pushes=day_pushes,
            seed_key=date_str,
        )
        if day_comment is not None:
            out.append("")
            out.append(day_comment.text)

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
