"""Per-tier YTD ledger for full-game picks.

Mirrors `nrfi/ledger.py` and `props_prizepicks/ledger.py` for visual +
behavioural consistency. Idempotent settlement on
`(game_pk, market_type, side, line_value)`. Per-tier W/L + units
rollup with `'ALL'` cross-tier and cross-market rollups.

Independent ledger from NRFI's and Props' — full-game markets have a
different variance profile (totals differ from props differ from
first-inning), so combining their ROIs would dilute the per-tier
signal. Each engine keeps its own.

Tables (DuckDB)
~~~~~~~~~~~~~~~

`fullgame_pick_settled` — one row per `(game_pk, market_type, side,
line_value)`::

    game_pk        BIGINT
    market_type    VARCHAR        'ML' / 'Run_Line' / 'Total' / ...
    side           VARCHAR        'Over' / 'Under' / tricode
    team_tricode   VARCHAR        for team-side markets
    line_value     DOUBLE
    season         INTEGER
    tier           VARCHAR
    predicted_p    DOUBLE
    american_odds  DOUBLE
    actual_home    INTEGER        home runs realized
    actual_away    INTEGER        away runs realized
    actual_hit     BOOLEAN
    units_delta    DOUBLE
    settled_at     TIMESTAMP

`fullgame_tier_ledger` — aggregated per (season, market_type, tier)
plus 'ALL' rollups.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Sequence

from edge_equation.engines.tiering import Tier
from edge_equation.utils.kelly import american_to_decimal
from edge_equation.utils.logging import get_logger

from .config import FullGameConfig, get_default_config
from .data.storage import FullGameStore

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


_DDL_PICK_SETTLED = """
CREATE TABLE IF NOT EXISTS fullgame_pick_settled (
    game_pk        BIGINT,
    market_type    VARCHAR,
    side           VARCHAR,
    team_tricode   VARCHAR,
    line_value     DOUBLE,
    season         INTEGER,
    tier           VARCHAR,
    predicted_p    DOUBLE,
    american_odds  DOUBLE,
    actual_home    INTEGER,
    actual_away    INTEGER,
    actual_hit     BOOLEAN,
    units_delta    DOUBLE,
    settled_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (game_pk, market_type, side, line_value)
)
"""


_DDL_TIER_LEDGER = """
CREATE TABLE IF NOT EXISTS fullgame_tier_ledger (
    season         INTEGER,
    market_type    VARCHAR,
    tier           VARCHAR,
    n_settled      INTEGER,
    wins           INTEGER,
    losses         INTEGER,
    units_won      DOUBLE,
    last_updated   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (season, market_type, tier)
)
"""


def init_ledger_tables(store: FullGameStore) -> None:
    """Idempotent — safe to call on every settlement run."""
    store.execute(_DDL_PICK_SETTLED)
    store.execute(_DDL_TIER_LEDGER)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_payout_units(american_odds: float) -> float:
    return american_to_decimal(american_odds) - 1.0


def _did_side_hit(
    market: str, side: str, team_tricode: str,
    home_tricode: str, line_value: Optional[float],
    actual_home: int, actual_away: int,
    f5_home: Optional[int] = None, f5_away: Optional[int] = None,
) -> bool:
    """Determine whether the staked side covered the line.

    Standard book conventions:
    * Totals — strict inequality on half-integer lines (Over=actual>line);
      integer lines push (treated here as miss for accounting honesty —
      operator can add explicit push handling later).
    * ML — winner takes it; tie impossible in MLB regular season.
    * Run_Line — favourite -1.5 covers when home_margin ≥ 2; dog +1.5
      covers when home_margin ≥ -1 (i.e. lose by 1 or win).
    """
    s = side.strip().lower()
    is_home_side = bool(team_tricode and team_tricode == home_tricode)
    market_norm = market.strip()

    if market_norm == "Total":
        if line_value is None:
            return False
        total = (actual_home or 0) + (actual_away or 0)
        if s == "over":
            return total > line_value
        return total < line_value

    if market_norm == "F5_Total":
        if line_value is None or f5_home is None or f5_away is None:
            return False
        total = (f5_home or 0) + (f5_away or 0)
        if s == "over":
            return total > line_value
        return total < line_value

    if market_norm == "Team_Total":
        if line_value is None:
            return False
        team_runs = actual_home if is_home_side else actual_away
        if team_runs is None:
            return False
        if s == "over":
            return team_runs > line_value
        return team_runs < line_value

    if market_norm == "ML":
        if is_home_side:
            return actual_home > actual_away
        return actual_away > actual_home

    if market_norm == "F5_ML":
        if f5_home is None or f5_away is None:
            return False
        if is_home_side:
            return f5_home > f5_away
        return f5_away > f5_home

    if market_norm == "Run_Line":
        if line_value is None:
            return False
        # The "team's margin" is positive when they win; line_value is
        # the team-specific spread (e.g., -1.5 for favorite, +1.5 for
        # dog). The team covers when margin > -line_value (so a -1.5
        # favorite needs margin > 1.5 = win by 2+).
        if is_home_side:
            margin = actual_home - actual_away
        else:
            margin = actual_away - actual_home
        threshold = -float(line_value)
        return margin > threshold

    return False


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


@dataclass
class SettlementResult:
    """Outcome of a single settlement pass."""
    n_picks_examined: int = 0
    n_picks_already_settled: int = 0
    n_picks_settled: int = 0
    n_picks_no_actual: int = 0
    by_tier: dict[Tier, int] | None = None

    def __post_init__(self) -> None:
        if self.by_tier is None:
            self.by_tier = {t: 0 for t in Tier}

    def summary(self) -> str:
        lines = [
            "Full-game settlement run",
            "─" * 40,
            f"  picks examined         {self.n_picks_examined}",
            f"  already settled        {self.n_picks_already_settled}",
            f"  newly settled          {self.n_picks_settled}",
            f"  no actual yet          {self.n_picks_no_actual}",
        ]
        if self.n_picks_settled > 0:
            lines.append("  by tier:")
            for tier, n in (self.by_tier or {}).items():
                if n > 0:
                    lines.append(f"    {tier.value:<10} {n}")
        return "\n".join(lines)


def settle_predictions(
    store: FullGameStore, *,
    season: int,
    cutoff_date: Optional[str] = None,
    config: Optional[FullGameConfig] = None,
) -> SettlementResult:
    """Settle every fullgame_predictions row that has a matching actual.

    Idempotent — already-settled `(game_pk, market, side, line)` tuples
    are skipped via the fullgame_pick_settled primary key.
    """
    cfg = (config or get_default_config()).resolve_paths()
    init_ledger_tables(store)

    cutoff = cutoff_date or date.today().isoformat()

    df = store.query_df(
        """
        SELECT p.game_pk, p.market_type, p.side, p.team_tricode,
               p.line_value, p.tier, p.american_odds,
               p.model_prob AS predicted_p,
               p.event_date,
               a.home_runs, a.away_runs,
               a.f5_home_runs, a.f5_away_runs
        FROM fullgame_predictions p
        JOIN fullgame_actuals     a USING(game_pk)
        WHERE p.event_date <= ?
          AND CAST(strftime(p.event_date, '%Y') AS INTEGER) = ?
        ORDER BY p.event_date
        """,
        (cutoff, int(season)),
    )

    result = SettlementResult()
    if df is None or df.empty:
        return result
    result.n_picks_examined = int(len(df))

    settled = store.query_df(
        """
        SELECT game_pk, market_type, side, line_value
        FROM fullgame_pick_settled
        WHERE season = ?
        """,
        (int(season),),
    )
    settled_keys = {
        (int(r.game_pk), str(r.market_type), str(r.side),
           float(r.line_value) if r.line_value is not None else None)
        for _, r in settled.iterrows()
    } if settled is not None and not settled.empty else set()

    new_rows: list[dict] = []
    for _, row in df.iterrows():
        tier_str = str(row.tier or "")
        try:
            tier_obj = Tier(tier_str)
        except ValueError:
            continue
        if not tier_obj.is_qualifying:
            continue

        line_val = (
            float(row.line_value) if row.line_value is not None
            and row.line_value == row.line_value  # NaN-safe
            else None
        )
        key = (int(row.game_pk), str(row.market_type), str(row.side),
                 line_val)
        if key in settled_keys:
            result.n_picks_already_settled += 1
            continue

        ah = row.home_runs
        aa = row.away_runs
        if ah != ah or aa != aa:  # NaN guard
            continue

        # Home tricode comes from the predictions row's team_tricode
        # only for team-side markets; for over/under markets we infer
        # via the actuals join (home/away_runs columns alone don't tell
        # us which team is home — but they also don't matter for the
        # totals/team_total settlement which works on the runs values).
        home_tricode = str(row.team_tricode or "")
        team_tri = str(row.team_tricode or "")

        f5_home = row.f5_home_runs if row.f5_home_runs == row.f5_home_runs else None
        f5_away = row.f5_away_runs if row.f5_away_runs == row.f5_away_runs else None
        try:
            f5_home_int = int(f5_home) if f5_home is not None else None
            f5_away_int = int(f5_away) if f5_away is not None else None
        except (TypeError, ValueError):
            f5_home_int = None
            f5_away_int = None

        hit = _did_side_hit(
            str(row.market_type), str(row.side), team_tri, home_tricode,
            line_val, int(ah), int(aa),
            f5_home=f5_home_int, f5_away=f5_away_int,
        )
        odds = float(row.american_odds)
        units = _pick_payout_units(odds) if hit else -1.0

        new_rows.append({
            "game_pk": int(row.game_pk),
            "market_type": str(row.market_type),
            "side": str(row.side),
            "team_tricode": team_tri,
            "line_value": line_val if line_val is not None else 0.0,
            "season": int(season),
            "tier": tier_obj.value,
            "predicted_p": float(row.predicted_p),
            "american_odds": odds,
            "actual_home": int(ah),
            "actual_away": int(aa),
            "actual_hit": bool(hit),
            "units_delta": float(units),
        })
        result.by_tier[tier_obj] = result.by_tier.get(tier_obj, 0) + 1
        result.n_picks_settled += 1

    if new_rows:
        store.upsert("fullgame_pick_settled", new_rows)
        _refresh_tier_ledger(store, season=season)

    result.n_picks_no_actual = (
        result.n_picks_examined
        - result.n_picks_already_settled
        - result.n_picks_settled
    )
    return result


def _refresh_tier_ledger(store: FullGameStore, *, season: int) -> None:
    """Re-aggregate `fullgame_tier_ledger` from settled picks."""
    rows = store.query_df(
        """
        SELECT season, market_type, tier,
               COUNT(*)          AS n_settled,
               SUM(CASE WHEN actual_hit THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN actual_hit THEN 0 ELSE 1 END) AS losses,
               SUM(units_delta)  AS units_won
        FROM fullgame_pick_settled
        WHERE season = ?
        GROUP BY season, market_type, tier
        """,
        (int(season),),
    )
    by_tier_market: list[dict] = []
    if rows is not None and not rows.empty:
        for _, r in rows.iterrows():
            by_tier_market.append({
                "season": int(r.season),
                "market_type": str(r.market_type),
                "tier": str(r.tier),
                "n_settled": int(r.n_settled),
                "wins": int(r.wins),
                "losses": int(r.losses),
                "units_won": float(r.units_won),
            })

    market_rollups = store.query_df(
        """
        SELECT season, market_type,
               COUNT(*)          AS n_settled,
               SUM(CASE WHEN actual_hit THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN actual_hit THEN 0 ELSE 1 END) AS losses,
               SUM(units_delta)  AS units_won
        FROM fullgame_pick_settled
        WHERE season = ?
        GROUP BY season, market_type
        """,
        (int(season),),
    )
    if market_rollups is not None and not market_rollups.empty:
        for _, r in market_rollups.iterrows():
            by_tier_market.append({
                "season": int(r.season),
                "market_type": str(r.market_type),
                "tier": "ALL",
                "n_settled": int(r.n_settled),
                "wins": int(r.wins),
                "losses": int(r.losses),
                "units_won": float(r.units_won),
            })

    total = store.query_df(
        """
        SELECT season,
               COUNT(*)          AS n_settled,
               SUM(CASE WHEN actual_hit THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN actual_hit THEN 0 ELSE 1 END) AS losses,
               SUM(units_delta)  AS units_won
        FROM fullgame_pick_settled
        WHERE season = ?
        GROUP BY season
        """,
        (int(season),),
    )
    if total is not None and not total.empty:
        for _, r in total.iterrows():
            by_tier_market.append({
                "season": int(r.season),
                "market_type": "ALL",
                "tier": "ALL",
                "n_settled": int(r.n_settled),
                "wins": int(r.wins),
                "losses": int(r.losses),
                "units_won": float(r.units_won),
            })

    store.execute(
        "DELETE FROM fullgame_tier_ledger WHERE season = ?",
        (int(season),),
    )
    if by_tier_market:
        store.upsert("fullgame_tier_ledger", by_tier_market)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def get_tier_ledger(store: FullGameStore, season: int):
    """Return the ledger as a DataFrame ordered by (market, tier)."""
    df = store.query_df(
        """
        SELECT season, market_type, tier,
               n_settled, wins, losses, units_won, last_updated
        FROM fullgame_tier_ledger
        WHERE season = ?
        """,
        (int(season),),
    )
    if df is None or df.empty:
        return df
    tier_order = {t.value: i for i, t in enumerate(
        [Tier.LOCK, Tier.STRONG, Tier.MODERATE, Tier.LEAN]
    )}
    tier_order["ALL"] = 99
    df["_tier_rank"] = df["tier"].map(lambda t: tier_order.get(t, 100))
    market_order = {m: i for i, m in enumerate(
        ["ML", "Run_Line", "Total", "F5_Total", "F5_ML", "Team_Total"]
    )}
    market_order["ALL"] = 999
    df["_market_rank"] = df["market_type"].map(
        lambda m: market_order.get(m, 100),
    )
    df = df.sort_values(["_market_rank", "_tier_rank"]).drop(
        columns=["_tier_rank", "_market_rank"],
    )
    return df.reset_index(drop=True)


def render_ledger_section(store: FullGameStore, season: int) -> str:
    """Plain-text YTD ledger block for the daily email."""
    df = get_tier_ledger(store, season)
    if df is None or df.empty:
        return ""

    lines = [f"FULL-GAME YTD LEDGER ({season})", "─" * 60]
    fmt = (
        "  {market:<11} {tier:<9} {record:>10}  "
        "{units:>+8.2f}u  {roi:>+6.1f}%"
    )
    for _, row in df.iterrows():
        market = str(row.market_type)
        tier = str(row.tier)
        wins = int(row.wins)
        losses = int(row.losses)
        units = float(row.units_won)
        n = int(row.n_settled)
        roi = (units / max(1, n)) * 100.0
        lines.append(fmt.format(
            market=market, tier=tier, record=f"{wins}-{losses}",
            units=units, roi=roi,
        ))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Full-game per-tier YTD ledger — settle / show",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_settle = sub.add_parser("settle", help="Run a settlement pass")
    p_settle.add_argument("--season", type=int, default=date.today().year)
    p_settle.add_argument("--cutoff-date", default=None)

    p_show = sub.add_parser("show", help="Print the YTD ledger")
    p_show.add_argument("--season", type=int, default=date.today().year)

    args = parser.parse_args(list(argv) if argv is not None else None)
    cfg = get_default_config().resolve_paths()
    store = FullGameStore(cfg.duckdb_path)

    if args.cmd == "settle":
        result = settle_predictions(
            store, season=args.season, cutoff_date=args.cutoff_date,
        )
        print(result.summary())
        return 0
    if args.cmd == "show":
        print(render_ledger_section(store, season=args.season) or
              f"(no settled full-game picks for {args.season} yet)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
