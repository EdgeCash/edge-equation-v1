"""
That K Report -- season ledger (K props).

Tiny JSON-backed ledger that tracks win/loss against the over/under
for every graded K projection once results land. The renderer reads
it to print the "Season Ledger (K Props)" footer on the Results
card. Writes are idempotent: the same (date, pitcher) never double-
counts, so replaying a settled day is safe.

Keep this file as a self-contained dependency-free store. A Turso
/ SQLite migration can come later without touching the renderer.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# Canonical default location.  Workflow passes an absolute path so
# this only bites the CLI dry-run path.
DEFAULT_LEDGER_PATH = Path("data/thatk_ledger.json")

# Settlement verdicts. "push" happens only on exact-line integer Ks
# (rare) -- books typically refund those, so the ledger treats them
# as neither a win nor a loss.
VERDICT_HIT = "hit"
VERDICT_MISS = "miss"
VERDICT_PUSH = "push"
_ALLOWED_VERDICTS = frozenset({VERDICT_HIT, VERDICT_MISS, VERDICT_PUSH})


@dataclass
class LedgerSnapshot:
    """Current all-time totals for the account."""
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    updated_at: Optional[str] = None
    # Dedup keys we've already counted -- tuple (date, pitcher, line).
    seen: List[List[str]] = field(default_factory=list)

    def total_graded(self) -> int:
        """Graded = W + L. Pushes are refunds; they don't move the
        hit-rate denominator."""
        return self.wins + self.losses

    def hit_rate(self) -> float:
        n = self.total_graded()
        if n == 0:
            return 0.0
        return self.wins / n

    def to_dict(self) -> dict:
        return {
            "wins": int(self.wins),
            "losses": int(self.losses),
            "pushes": int(self.pushes),
            "updated_at": self.updated_at,
            "seen": [list(k) for k in self.seen],
        }

    @staticmethod
    def from_dict(d: Optional[dict]) -> "LedgerSnapshot":
        d = d or {}
        return LedgerSnapshot(
            wins=int(d.get("wins", 0)),
            losses=int(d.get("losses", 0)),
            pushes=int(d.get("pushes", 0)),
            updated_at=d.get("updated_at"),
            seen=[list(k) for k in (d.get("seen") or [])],
        )


class Ledger:
    """File-backed ledger. Open / read / mutate / flush explicitly.

    Not thread-safe by design -- the caller runs once per cron tick
    and exits. Keeping it that simple lets us move to SQLite later
    without rewriting callers.
    """

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH):
        self.path = Path(path)
        self._snap = LedgerSnapshot()
        self._loaded = False

    # --------------------------------------------------- IO

    def load(self) -> LedgerSnapshot:
        if self._loaded:
            return self._snap
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
        else:
            raw = {}
        self._snap = LedgerSnapshot.from_dict(raw)
        self._loaded = True
        return self._snap

    def flush(self) -> None:
        self._snap.updated_at = datetime.utcnow().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._snap.to_dict(), indent=2, sort_keys=False),
            encoding="utf-8",
        )

    # --------------------------------------------------- mutation

    @staticmethod
    def _dedup_key(date: str, pitcher: str, line: str) -> Tuple[str, str, str]:
        return (str(date), str(pitcher).strip(), str(line).strip())

    def record(
        self,
        date: str,
        pitcher: str,
        line: str,
        verdict: str,
    ) -> bool:
        """Mark one result against the book's line. Returns True when
        the row was newly counted; False when it was a dedup hit so
        the caller can log re-runs honestly."""
        if verdict not in _ALLOWED_VERDICTS:
            raise ValueError(
                f"verdict must be one of {_ALLOWED_VERDICTS}, got {verdict!r}"
            )
        self.load()
        key = self._dedup_key(date, pitcher, line)
        key_list = list(key)
        if key_list in self._snap.seen:
            return False
        if verdict == VERDICT_HIT:
            self._snap.wins += 1
        elif verdict == VERDICT_MISS:
            self._snap.losses += 1
        else:  # push
            self._snap.pushes += 1
        self._snap.seen.append(key_list)
        return True

    def record_many(
        self, date: str, rows: Iterable[Tuple[str, str, str]],
    ) -> int:
        """Bulk record: rows is an iterable of (pitcher, line, verdict).
        Returns the number of rows newly counted."""
        counted = 0
        for pitcher, line, verdict in rows:
            if self.record(date=date, pitcher=pitcher, line=line, verdict=verdict):
                counted += 1
        return counted

    # --------------------------------------------------- view

    def summary(self) -> LedgerSnapshot:
        self.load()
        return self._snap


def verdict_for_line(actual: int, line: float) -> str:
    """Classify a result against the book's line.  Whole-number lines
    can push; half-integer lines always resolve."""
    if actual > line:
        return VERDICT_HIT
    if actual < line:
        return VERDICT_MISS
    return VERDICT_PUSH
