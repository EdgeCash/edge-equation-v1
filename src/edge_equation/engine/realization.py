"""
RealizationTracker: settle stored picks against known outcomes.

After upstream ingests actual game outcomes (manual CSV load, API callback,
or scrape), RealizationTracker walks the pick / realization join and updates
each matching pick's realization field.

Encoding (per-pick integer):
- 100  win
-   0  loss
-  50  push (stake returned, no P&L)
-  -1  void (cancelled; excluded from P&L)

Unsettled picks retain their forecast realization (47 for C, 52 for B, etc).
All operations are idempotent -- re-running settle_picks for the same
(game_id, market_type, selection) yields the same end state.
"""
import sqlite3
from typing import Dict, List, Optional

from edge_equation.persistence.realization_store import RealizationStore


SETTLED_WIN = 100
SETTLED_LOSS = 0
SETTLED_PUSH = 50
SETTLED_VOID = -1

_OUTCOME_TO_VALUE = {
    "win": SETTLED_WIN,
    "loss": SETTLED_LOSS,
    "push": SETTLED_PUSH,
    "void": SETTLED_VOID,
}


class RealizationTracker:
    """
    Coordinator between PickStore and RealizationStore:
    - realization_value(outcome)        -> int
    - settle_picks(conn, slate_id=None) -> {"updated": N, "matched": M}
    - hit_rate_by_grade(conn, sport=None) -> {grade: {"n": n, "wins": w, "hit_rate": float}}
    """

    @staticmethod
    def realization_value(outcome: str) -> int:
        if outcome not in _OUTCOME_TO_VALUE:
            raise ValueError(
                f"unknown outcome {outcome!r}; expected one of {sorted(_OUTCOME_TO_VALUE)}"
            )
        return _OUTCOME_TO_VALUE[outcome]

    @staticmethod
    def settle_picks(
        conn: sqlite3.Connection,
        slate_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        For every (pick, outcome) pair that matches on (game_id, market_type,
        selection), write the outcome-derived realization value back to the
        pick row. Optionally scoped to a single slate.
        """
        if slate_id is None:
            rows = conn.execute(
                """
                SELECT p.id AS pick_id, r.outcome AS outcome, p.realization AS current
                FROM picks p
                JOIN realizations r
                  ON p.game_id = r.game_id
                 AND p.market_type = r.market_type
                 AND p.selection = r.selection
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT p.id AS pick_id, r.outcome AS outcome, p.realization AS current
                FROM picks p
                JOIN realizations r
                  ON p.game_id = r.game_id
                 AND p.market_type = r.market_type
                 AND p.selection = r.selection
                WHERE p.slate_id = ?
                """,
                (slate_id,),
            ).fetchall()

        matched = len(rows)
        updated = 0
        for r in rows:
            new_val = RealizationTracker.realization_value(r["outcome"])
            if int(r["current"]) != new_val:
                conn.execute(
                    "UPDATE picks SET realization = ? WHERE id = ?",
                    (new_val, int(r["pick_id"])),
                )
                updated += 1
        conn.commit()
        return {"matched": matched, "updated": updated}

    @staticmethod
    def hit_rate_since(
        conn: sqlite3.Connection,
        since_iso: str,
        sport: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        Aggregate hit rate across every pick whose recorded_at is at-or-
        after the given ISO timestamp. Returns a scalar summary rather
        than a per-grade breakdown -- used by the premium daily email to
        render yesterday's engine health.
        """
        base = """
            SELECT
                COUNT(*) AS n,
                SUM(CASE WHEN realization = 100 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN realization = 0   THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN realization = 50  THEN 1 ELSE 0 END) AS pushes
            FROM picks
            WHERE realization IN (0, 50, 100)
              AND recorded_at >= ?
        """
        args = [since_iso]
        if sport is not None:
            base += " AND sport = ?"
            args.append(sport)
        row = conn.execute(base, args).fetchone()
        n = int(row["n"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        pushes = int(row["pushes"] or 0)
        denom = wins + losses
        hit = (wins / denom) if denom > 0 else 0.0
        return {
            "n": n,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "hit_rate": hit,
        }

    @staticmethod
    def hit_rate_by_grade(
        conn: sqlite3.Connection,
        sport: Optional[str] = None,
    ) -> Dict[str, Dict[str, float]]:
        """
        Bucket settled picks by grade and report hit rate. A pick is 'settled'
        iff its realization is one of 0, 50, 100 (void excluded from both
        numerator and denominator).
        """
        base = """
            SELECT grade,
                   COUNT(*) AS n,
                   SUM(CASE WHEN realization = 100 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN realization = 50  THEN 1 ELSE 0 END) AS pushes
            FROM picks
            WHERE realization IN (0, 50, 100)
        """
        if sport is not None:
            rows = conn.execute(base + " AND sport = ? GROUP BY grade", (sport,)).fetchall()
        else:
            rows = conn.execute(base + " GROUP BY grade").fetchall()

        result: Dict[str, Dict[str, float]] = {}
        for r in rows:
            n = int(r["n"])
            wins = int(r["wins"])
            pushes = int(r["pushes"])
            # Hit rate: wins / (non-push settled); if all pushes, 0.0.
            denom = n - pushes
            hit = (wins / denom) if denom > 0 else 0.0
            result[r["grade"]] = {
                "n": n,
                "wins": wins,
                "pushes": pushes,
                "hit_rate": hit,
            }
        return result
