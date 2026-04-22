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
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from edge_equation.persistence.realization_store import RealizationStore


SETTLED_WIN = 100
SETTLED_LOSS = 0
SETTLED_PUSH = 50
SETTLED_VOID = -1

# Private aliases used by settle_picks_from_game_results; kept local
# so touching the constants doesn't ripple elsewhere in the module.
_WIN = SETTLED_WIN
_LOSS = SETTLED_LOSS
_PUSH = SETTLED_PUSH

# Default realization value for an unsettled pick (forecast only).
# Mirrors the value Pick.realization defaults to at construction time
# when no outcome has been observed yet.
_PENDING_DEFAULT = 47

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
    - settle_picks_from_game_results(conn) -> {"updated": N, ...}
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
    def settle_pick_vs_result(
        market_type: str,
        selection: str,
        home_team: str,
        away_team: str,
        home_score: int,
        away_score: int,
    ) -> Optional[int]:
        """Compute the realization value for one (pick, game-result) pair
        from scores and market metadata. Returns None when the market
        isn't auto-settleable (player props, unknown selection format,
        etc.) -- the caller leaves those at pending=47 so a human /
        separate results pipeline can still fill them.

        Handles ML (moneyline), Total / Game_Total, and
        Run_Line / Puck_Line / Spread markets. All selection formats
        match what TheOddsApiSource._selection_name emits."""
        sel = (selection or "").strip()
        if not sel:
            return None
        # ---------- Moneyline
        if market_type == "ML":
            if home_score == away_score:
                return _PUSH
            home_won = home_score > away_score
            if sel == home_team:
                return _WIN if home_won else _LOSS
            if sel == away_team:
                return _WIN if not home_won else _LOSS
            return None
        # ---------- Totals ("Over 9.5" / "Under 9.5")
        if market_type in ("Total", "Game_Total"):
            parts = sel.split()
            if len(parts) < 2:
                return None
            side = parts[0].lower()
            try:
                point = Decimal(parts[-1])
            except (InvalidOperation, ValueError):
                return None
            total = Decimal(home_score + away_score)
            if side == "over":
                if total > point: return _WIN
                if total < point: return _LOSS
                return _PUSH
            if side == "under":
                if total < point: return _WIN
                if total > point: return _LOSS
                return _PUSH
            return None
        # ---------- Spread family ("Team +1.5" / "Team -1.5")
        if market_type in ("Run_Line", "Puck_Line", "Spread"):
            # Split off the trailing signed number; everything else is
            # the team name (handles multi-word names).
            tokens = sel.rsplit(" ", 1)
            if len(tokens) != 2:
                return None
            team, num = tokens
            try:
                point = Decimal(num)
            except (InvalidOperation, ValueError):
                return None
            if team == home_team:
                adjusted = (home_score + float(point)) - away_score
            elif team == away_team:
                adjusted = (away_score + float(point)) - home_score
            else:
                return None
            if adjusted > 0: return _WIN
            if adjusted < 0: return _LOSS
            return _PUSH
        # ---------- Props and others: not auto-settleable.
        return None

    @staticmethod
    def settle_picks_from_game_results(
        conn: sqlite3.Connection,
    ) -> Dict[str, int]:
        """Walk every pending pick (realization == PENDING_DEFAULT) and
        settle the ones whose game_id matches a row in game_results.
        Uses the score + selection to compute outcome deterministically
        without the realizations CSV path. Safe to re-run: only touches
        rows currently at pending, never overwrites a resolved pick.

        Returns per-status counts so the nightly settler can log a
        clean audit line.
        """
        rows = conn.execute(
            """
            SELECT p.id AS pick_id,
                   p.market_type AS market_type,
                   p.selection   AS selection,
                   p.realization AS current_realization,
                   g.home_team   AS home_team,
                   g.away_team   AS away_team,
                   g.home_score  AS home_score,
                   g.away_score  AS away_score
              FROM picks p
              JOIN game_results g ON g.game_id = p.game_id
             WHERE p.realization = ?
               AND g.status = 'final'
            """,
            (_PENDING_DEFAULT,),
        ).fetchall()

        updated = 0
        unmatchable = 0
        for r in rows:
            new_val = RealizationTracker.settle_pick_vs_result(
                market_type=r["market_type"],
                selection=r["selection"],
                home_team=r["home_team"],
                away_team=r["away_team"],
                home_score=int(r["home_score"]),
                away_score=int(r["away_score"]),
            )
            if new_val is None:
                unmatchable += 1
                continue
            conn.execute(
                "UPDATE picks SET realization = ? WHERE id = ?",
                (new_val, int(r["pick_id"])),
            )
            updated += 1
        conn.commit()
        return {
            "matched": len(rows),
            "updated": updated,
            "unmatchable": unmatchable,
        }

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
