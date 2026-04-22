"""
Season Ledger footer.

Phase 20 brand rule: every free X post must end with

    Season Ledger: W-L-T +U.UU units +R.R ROI | Bet within your means. Problem? Call 1-800-GAMBLER.

W-L-T is the running season record (wins, losses, pushes). Units and ROI
are derived from the Pick stake + realization columns the engine already
writes. No feelings, no forecasts -- just the verified data we actually
have.

This module is deterministic: same (PickStore + RealizationStore) inputs
yield identical footer text. Assumes 1-unit stakes for public ledger
reporting (premium gets Kelly-sized units; free stays flat).
"""
from dataclasses import dataclass
from decimal import Decimal
import sqlite3
from typing import Optional

from edge_equation.engine.pick_schema import Line


# Flat-unit stake assumed for the public Season Ledger line. Premium
# subscribers see Kelly-sized units; free content stays on a single unit
# per play so the W-L-T and ROI numbers are easy to audit.
DEFAULT_UNIT_STAKE = Decimal('1.00')

# Settled-pick realization encodings (defined in engine.realization).
SETTLED_WIN = 100
SETTLED_LOSS = 0
SETTLED_PUSH = 50
SETTLED_VOID = -1


@dataclass(frozen=True)
class LedgerStats:
    """Running record + P&L for the free-content Season Ledger footer."""
    wins: int
    losses: int
    pushes: int
    units_net: Decimal
    roi_pct: Decimal
    total_plays: int

    def to_dict(self) -> dict:
        return {
            "wins": self.wins,
            "losses": self.losses,
            "pushes": self.pushes,
            "units_net": str(self.units_net),
            "roi_pct": str(self.roi_pct),
            "total_plays": self.total_plays,
        }


def format_ledger_footer(stats: LedgerStats) -> str:
    """
    Render the exact Phase 20 mandatory footer from a LedgerStats. Text:

        "Season Ledger: W-L-T +U.UU units +R.R ROI | Bet within your means.
         Problem? Call 1-800-GAMBLER."

    Signed + sign prefixes + two/one decimal precision are fixed by the
    brand spec.
    """
    units_sign = "+" if stats.units_net >= Decimal('0') else "-"
    roi_sign = "+" if stats.roi_pct >= Decimal('0') else "-"
    units_str = f"{units_sign}{abs(stats.units_net):.2f}"
    roi_str = f"{roi_sign}{abs(stats.roi_pct):.1f}"
    return (
        f"Season Ledger: {stats.wins}-{stats.losses}-{stats.pushes} "
        f"{units_str} units {roi_str} ROI | "
        f"Bet within your means. Problem? Call 1-800-GAMBLER."
    )


def _american_to_net_decimal(odds: int) -> Decimal:
    """Decimal multiplier of profit per unit staked for American odds."""
    if odds == 0:
        return Decimal('0')
    if odds > 0:
        return Decimal(odds) / Decimal('100')
    return Decimal('100') / Decimal(-odds)


class LedgerStore:
    """
    Reads picks + realization from SQLite and composes a LedgerStats:
    - compute(conn, sport=None, unit_stake=DEFAULT_UNIT_STAKE) -> LedgerStats

    Treats every Pick with realization IN (0, 50, 100) as a settled play.
    Void picks (realization == -1) are excluded from counts and P&L. If
    the picks table is empty (fresh deploy) returns a zeroed LedgerStats.
    """

    @staticmethod
    def compute(
        conn: sqlite3.Connection,
        sport: Optional[str] = None,
        unit_stake: Decimal = DEFAULT_UNIT_STAKE,
    ) -> LedgerStats:
        base_sql = (
            "SELECT realization, odds FROM picks "
            "WHERE realization IN (?, ?, ?)"
        )
        args: list = [SETTLED_WIN, SETTLED_LOSS, SETTLED_PUSH]
        if sport is not None:
            base_sql += " AND sport = ?"
            args.append(sport)
        rows = conn.execute(base_sql, args).fetchall()

        wins = 0
        losses = 0
        pushes = 0
        units_net = Decimal('0')
        total_staked = Decimal('0')
        for row in rows:
            realization = int(row["realization"])
            odds = int(row["odds"]) if row["odds"] is not None else -110
            total_staked += unit_stake
            if realization == SETTLED_WIN:
                wins += 1
                units_net += _american_to_net_decimal(odds) * unit_stake
            elif realization == SETTLED_LOSS:
                losses += 1
                units_net -= unit_stake
            elif realization == SETTLED_PUSH:
                pushes += 1
                # no P&L change on a push
        roi_pct = (
            (units_net / total_staked * Decimal('100')).quantize(Decimal('0.1'))
            if total_staked > Decimal('0') else Decimal('0.0')
        )
        return LedgerStats(
            wins=wins,
            losses=losses,
            pushes=pushes,
            units_net=units_net.quantize(Decimal('0.01')),
            roi_pct=roi_pct,
            total_plays=wins + losses + pushes,
        )
