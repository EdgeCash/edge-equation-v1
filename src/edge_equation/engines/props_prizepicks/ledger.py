"""Per-tier YTD ledger for player-prop picks.

Mirrors `nrfi/ledger.py` for visual + behavioural consistency:

* Idempotent settlement on `(game_pk, market_type, player_name)`.
* Per-tier W/L + units rollup (LOCK / STRONG / MODERATE / LEAN), plus
  cross-tier `'ALL'` rollups for the headline summary.
* Independent ledger from NRFI's — props are tracked separately so
  per-tier ROI numbers don't get diluted across markets with different
  natural variance profiles.

Tables (DuckDB)
~~~~~~~~~~~~~~~

`props_pick_settled` — one row per `(game_pk, market_type, player_name)`
that's been settled. Idempotent — settlement is safe to re-run::

    game_pk        BIGINT
    market_type    VARCHAR        'HR' / 'Hits' / 'K' / ...
    player_name    VARCHAR
    season         INTEGER
    line_value     DOUBLE
    side           VARCHAR        'Over' / 'Under'
    tier           VARCHAR        'LOCK' / 'STRONG' / 'MODERATE' / 'LEAN'
    predicted_p    DOUBLE
    american_odds  DOUBLE
    actual_value   DOUBLE         realised count
    actual_hit     BOOLEAN
    units_delta    DOUBLE         +payout or -1
    settled_at     TIMESTAMP

`props_tier_ledger` — aggregated per (season, market_type, tier) for
fast lookup. Re-derivable from props_pick_settled at any time. Mirrors
`nrfi_tier_ledger`'s shape so callers can route both ledgers through
one rendering helper if they want a unified board later.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Sequence

from edge_equation.engines.tiering import Tier
from edge_equation.utils.kelly import american_to_decimal
from edge_equation.utils.logging import get_logger

from .config import PropsConfig, get_default_config
from .data.storage import PropsStore

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


_DDL_PICK_SETTLED = """
CREATE TABLE IF NOT EXISTS props_pick_settled (
    game_pk        BIGINT,
    market_type    VARCHAR,
    player_name    VARCHAR,
    season         INTEGER,
    line_value     DOUBLE,
    side           VARCHAR,
    tier           VARCHAR,
    predicted_p    DOUBLE,
    american_odds  DOUBLE,
    actual_value   DOUBLE,
    actual_hit     BOOLEAN,
    units_delta    DOUBLE,
    settled_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (game_pk, market_type, player_name)
)
"""

_DDL_TIER_LEDGER = """
CREATE TABLE IF NOT EXISTS props_tier_ledger (
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


def init_ledger_tables(store: PropsStore) -> None:
    """Idempotent — safe to call on every settlement run."""
    store.execute(_DDL_PICK_SETTLED)
    store.execute(_DDL_TIER_LEDGER)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_payout_units(american_odds: float) -> float:
    """Win payout in units for a 1u stake at the given American odds."""
    return american_to_decimal(american_odds) - 1.0


def _did_side_hit(side: str, actual_value: float, line_value: float) -> bool:
    """Determine whether the staked side covered the line.

    Standard book convention: line at exactly the integer pushes (no
    win/loss). Half-integer lines (the common case) are unambiguous.
    """
    s = side.strip().lower()
    if s in ("over", "yes"):
        return float(actual_value) > float(line_value)
    if s in ("under", "no"):
        return float(actual_value) < float(line_value)
    # Unknown side label → treat as miss to keep accounting honest.
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
            "Props settlement run",
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
    store: PropsStore,
    *,
    season: int,
    cutoff_date: Optional[str] = None,
    config: Optional[PropsConfig] = None,
) -> SettlementResult:
    """Settle every prop_predictions row that has a matching prop_actuals.

    Idempotent — already-settled (game_pk, market, player) tuples are
    skipped via the props_pick_settled primary key.
    """
    cfg = (config or get_default_config()).resolve_paths()
    init_ledger_tables(store)

    cutoff = cutoff_date or date.today().isoformat()

    # Pull every prediction with a matching actual.
    df = store.query_df(
        """
        SELECT p.game_pk, p.market_type, p.player_name,
               p.line_value, p.side, p.tier, p.american_odds,
               p.model_prob AS predicted_p,
               p.event_date,
               a.actual_value
        FROM prop_predictions p
        JOIN prop_actuals     a USING(game_pk, market_type, player_name)
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
        SELECT game_pk, market_type, player_name FROM props_pick_settled
        WHERE season = ?
        """,
        (int(season),),
    )
    settled_keys = {
        (int(r.game_pk), str(r.market_type), str(r.player_name))
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

        key = (int(row.game_pk), str(row.market_type), str(row.player_name))
        if key in settled_keys:
            result.n_picks_already_settled += 1
            continue

        actual_value = row.actual_value
        if actual_value != actual_value:  # NaN guard
            continue

        hit = _did_side_hit(
            str(row.side), float(actual_value), float(row.line_value),
        )
        odds = float(row.american_odds)
        units = _pick_payout_units(odds) if hit else -1.0

        new_rows.append({
            "game_pk": int(row.game_pk),
            "market_type": str(row.market_type),
            "player_name": str(row.player_name),
            "season": int(season),
            "line_value": float(row.line_value),
            "side": str(row.side),
            "tier": tier_obj.value,
            "predicted_p": float(row.predicted_p),
            "american_odds": odds,
            "actual_value": float(actual_value),
            "actual_hit": bool(hit),
            "units_delta": float(units),
        })
        result.by_tier[tier_obj] = result.by_tier.get(tier_obj, 0) + 1
        result.n_picks_settled += 1

    if new_rows:
        store.upsert("props_pick_settled", new_rows)
        _refresh_tier_ledger(store, season=season)

    result.n_picks_no_actual = (
        result.n_picks_examined
        - result.n_picks_already_settled
        - result.n_picks_settled
    )
    return result


def _refresh_tier_ledger(store: PropsStore, *, season: int) -> None:
    """Re-aggregate `props_tier_ledger` from the settled-picks table.

    Wipe + reinsert is fine — the table is tiny (O(seasons × markets ×
    tiers)) and re-running settlement is cheap.
    """
    rows = store.query_df(
        """
        SELECT season, market_type, tier,
               COUNT(*)          AS n_settled,
               SUM(CASE WHEN actual_hit THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN actual_hit THEN 0 ELSE 1 END) AS losses,
               SUM(units_delta)  AS units_won
        FROM props_pick_settled
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
        FROM props_pick_settled
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
        FROM props_pick_settled
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
        "DELETE FROM props_tier_ledger WHERE season = ?",
        (int(season),),
    )
    if by_tier_market:
        store.upsert("props_tier_ledger", by_tier_market)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def get_tier_ledger(store: PropsStore, season: int):
    """Return the ledger as a DataFrame ordered by (market, tier)."""
    df = store.query_df(
        """
        SELECT season, market_type, tier,
               n_settled, wins, losses, units_won, last_updated
        FROM props_tier_ledger
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
        ["HR", "Hits", "Total_Bases", "RBI", "K"]
    )}
    market_order["ALL"] = 999
    df["_market_rank"] = df["market_type"].map(
        lambda m: market_order.get(m, 100),
    )
    df = df.sort_values(["_market_rank", "_tier_rank"]).drop(
        columns=["_tier_rank", "_market_rank"],
    )
    return df.reset_index(drop=True)


def render_ledger_section(store: PropsStore, season: int) -> str:
    """Plain-text YTD ledger block for the daily email / dashboard."""
    df = get_tier_ledger(store, season)
    if df is None or df.empty:
        return ""

    lines = [f"PROPS YTD LEDGER ({season})", "─" * 60]
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
            market=market, tier=tier,
            record=f"{wins}-{losses}",
            units=units, roi=roi,
        ))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Props per-tier YTD ledger — settle / show",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_settle = sub.add_parser("settle", help="Run a settlement pass")
    p_settle.add_argument("--season", type=int, default=date.today().year)
    p_settle.add_argument("--cutoff-date", default=None)

    p_show = sub.add_parser("show", help="Print the YTD ledger")
    p_show.add_argument("--season", type=int, default=date.today().year)

    args = parser.parse_args(list(argv) if argv is not None else None)
    cfg = get_default_config().resolve_paths()
    store = PropsStore(cfg.duckdb_path)

    if args.cmd == "settle":
        result = settle_predictions(
            store, season=args.season, cutoff_date=args.cutoff_date,
        )
        print(result.summary())
        return 0
    if args.cmd == "show":
        print(render_ledger_section(store, season=args.season) or
              f"(no settled props for {args.season} yet)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
