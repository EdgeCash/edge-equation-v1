"""Per-tier YTD ledger for NRFI/YRFI picks.

Every pick that clears LEAN (or higher) is persisted into the ledger
with its tier classification and, after the actual first-inning
outcome lands, settled into the running W/L + units record.

Tables (DuckDB)
---------------

`nrfi_pick_settled` — one row per `(game_pk, market_type)` pair we've
already counted. Idempotent — settlement is safe to re-run.

::

    game_pk        BIGINT
    market_type    VARCHAR    'NRFI' or 'YRFI'
    season         INTEGER
    tier           VARCHAR    'ELITE' / 'STRONG' / 'MODERATE' / 'LEAN'
    predicted_p    DOUBLE     calibrated model prob for the side staked
    american_odds  DOUBLE     odds we'd have gotten (default -120/-105)
    actual_hit     BOOLEAN    did the predicted side hit?
    units_delta    DOUBLE     +payout (win) or -1 (loss)
    settled_at     TIMESTAMP

`nrfi_tier_ledger` — aggregated per (season, market_type, tier) for
fast lookup. Re-derivable from nrfi_pick_settled at any time.

::

    season         INTEGER
    market_type    VARCHAR    or 'ALL' for the cross-side roll-up
    tier           VARCHAR    or 'ALL' for the cross-tier roll-up
    n_settled      INTEGER
    wins           INTEGER
    losses         INTEGER
    units_won      DOUBLE     net units (won − lost)
    last_updated   TIMESTAMP

Tracking rules
--------------

* **Stake**: flat 1u per qualifying pick. Comparing tiers by ROI is
  only meaningful when stakes are constant — Kelly-weighted ROI is a
  separate metric we'll add later if we want.
* **Side selection**: NRFI picks staked when `nrfi_prob >= LEAN
  threshold`. Otherwise we may stake the YRFI side instead (YRFI prob
  = 1 − NRFI prob; same ladder applied to it). For each game we stake
  at most one side — whichever clears the higher tier.
* **Odds**: default -120 NRFI / -105 YRFI per the existing
  `mlb_nrfi_source` defaults. Future PRs will replace these with live
  odds when an Odds API integration ships.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Sequence

from edge_equation.engines.tiering import Tier, classify_tier
from edge_equation.utils.kelly import american_to_decimal
from edge_equation.utils.logging import get_logger

from .config import NRFIConfig, get_default_config
from .data.scrapers_etl import backfill_actuals
from .data.storage import NRFIStore

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_NRFI_ODDS = -120.0
DEFAULT_YRFI_ODDS = -105.0


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


_DDL_PICK_SETTLED = """
CREATE TABLE IF NOT EXISTS nrfi_pick_settled (
    game_pk        BIGINT,
    market_type    VARCHAR,
    season         INTEGER,
    tier           VARCHAR,
    predicted_p    DOUBLE,
    american_odds  DOUBLE,
    actual_hit     BOOLEAN,
    units_delta    DOUBLE,
    settled_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (game_pk, market_type)
)
"""


_DDL_TIER_LEDGER = """
CREATE TABLE IF NOT EXISTS nrfi_tier_ledger (
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


def init_ledger_tables(store: NRFIStore) -> None:
    """Idempotent — safe to call on every settlement run."""
    store.execute(_DDL_PICK_SETTLED)
    store.execute(_DDL_TIER_LEDGER)


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
    by_tier: dict[Tier, int] | None = None  # tier -> count newly settled

    def __post_init__(self) -> None:
        if self.by_tier is None:
            self.by_tier = {t: 0 for t in Tier}

    def summary(self) -> str:
        lines = [
            f"Settlement run",
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


def _pick_payout_units(american_odds: float) -> float:
    """Win payout in units for a 1u stake at the given American odds."""
    return american_to_decimal(american_odds) - 1.0


def _lookup_closing_odds_safe(
    store: NRFIStore, game_pk: int, market_type: str,
) -> Optional[float]:
    """Wrapper around `odds.lookup_closing_odds` that swallows the
    ImportError on environments where the odds module's deps aren't
    installed, and the AssertionError DuckDB raises when the
    `nrfi_odds_snapshot` table doesn't exist yet (first settlement on
    a fresh DB before the daily-odds workflow has ever run).
    """
    try:
        from .data.odds import lookup_closing_odds
        return lookup_closing_odds(store, game_pk, market_type)
    except Exception as e:
        log.debug("closing-odds lookup unavailable for game_pk=%s "
                    "market=%s (%s): %s",
                    game_pk, market_type, type(e).__name__, e)
        return None


def _stake_side_for_game(nrfi_prob: float) -> tuple[str, float, float]:
    """Pick whichever side (NRFI or YRFI) clears the higher tier.

    Returns (market_type, side_probability, default_american_odds).
    The odds returned here are the engine's flat -120/-105 default;
    `settle_predictions` looks up a captured closing-line snapshot
    via `lookup_closing_odds` and overrides this default when one is
    present.

    When both sides are sub-LEAN we still return the higher one — the
    tier classifier downstream will mark it NO_PLAY and it won't be
    persisted into the ledger.
    """
    yrfi_prob = 1.0 - nrfi_prob
    nrfi_tier = classify_tier(market_type="NRFI", side_probability=nrfi_prob).tier
    yrfi_tier = classify_tier(market_type="YRFI", side_probability=yrfi_prob).tier

    # Tier ordering for selection — ELITE > STRONG > MODERATE > LEAN > NO_PLAY.
    rank = {Tier.ELITE: 4, Tier.STRONG: 3, Tier.MODERATE: 2,
            Tier.LEAN: 1, Tier.NO_PLAY: 0}
    if rank[nrfi_tier] >= rank[yrfi_tier]:
        return "NRFI", nrfi_prob, DEFAULT_NRFI_ODDS
    return "YRFI", yrfi_prob, DEFAULT_YRFI_ODDS


def settle_predictions(
    store: NRFIStore,
    *,
    season: int,
    cutoff_date: Optional[str] = None,
    config: Optional[NRFIConfig] = None,
    pull_actuals: bool = True,
) -> SettlementResult:
    """Walk every (predictions × actuals) row for `season`, classify the
    higher-tier side, and update the ledger.

    Parameters
    ----------
    cutoff_date : Inclusive upper-bound game_date. None means today.
    pull_actuals : When True, run `backfill_actuals` for the cutoff
        window first so any games that finished since the last
        settlement are picked up. Set False to skip the network call
        when you've already populated actuals via other means.
    """
    cfg = (config or get_default_config()).resolve_paths()
    init_ledger_tables(store)

    cutoff = cutoff_date or date.today().isoformat()

    # Optional: fill in any actuals we haven't yet recorded.
    if pull_actuals:
        season_start = f"{season}-03-01"  # MLB regular season earliest
        try:
            backfill_actuals(season_start, cutoff, store, config=cfg)
        except Exception as e:
            log.warning("backfill_actuals during settlement failed: %s", e)

    # Pull every prediction for the season that has an actual logged.
    df = store.query_df(
        """
        SELECT p.game_pk,
               p.nrfi_prob,
               p.lambda_total,
               g.game_date,
               g.season,
               a.nrfi      AS actual_nrfi,
               a.first_inn_runs
        FROM predictions p
        JOIN actuals  a USING(game_pk)
        JOIN games    g USING(game_pk)
        WHERE g.season = ?
          AND g.game_date <= ?
        ORDER BY g.game_date
        """,
        (int(season), cutoff),
    )

    result = SettlementResult()
    if df is None or df.empty:
        return result
    result.n_picks_examined = int(len(df))

    # Already-settled (game_pk, market_type) pairs.
    settled = store.query_df(
        """
        SELECT game_pk, market_type FROM nrfi_pick_settled
        WHERE season = ?
        """,
        (int(season),),
    )
    settled_keys = {
        (int(r.game_pk), str(r.market_type))
        for _, r in settled.iterrows()
    } if settled is not None and not settled.empty else set()

    # Walk per-game; pick the higher-tier side; settle.
    new_rows: list[dict] = []
    for _, row in df.iterrows():
        nrfi_prob = float(row.nrfi_prob)
        if nrfi_prob != nrfi_prob:  # NaN guard
            continue

        market_type, side_p, default_odds = _stake_side_for_game(nrfi_prob)
        clf = classify_tier(market_type=market_type, side_probability=side_p)
        if not clf.tier.is_qualifying:
            continue

        key = (int(row.game_pk), market_type)
        if key in settled_keys:
            result.n_picks_already_settled += 1
            continue

        # Phase 4: prefer a captured closing-line snapshot when one
        # exists; otherwise fall back to the engine's flat default.
        # `lookup_closing_odds` returns None when no snapshot was
        # captured (Odds API down, market not posted, plan-credit cap).
        captured = _lookup_closing_odds_safe(store, int(row.game_pk),
                                                market_type)
        odds = captured if captured is not None else default_odds

        # Outcome.
        actual_nrfi = bool(row.actual_nrfi)
        if market_type == "NRFI":
            hit = actual_nrfi
        else:  # YRFI
            hit = not actual_nrfi
        units = _pick_payout_units(odds) if hit else -1.0

        new_rows.append({
            "game_pk": int(row.game_pk),
            "market_type": market_type,
            "season": int(row.season),
            "tier": clf.tier.value,
            "predicted_p": float(side_p),
            "american_odds": float(odds),
            "actual_hit": bool(hit),
            "units_delta": float(units),
        })
        result.by_tier[clf.tier] = result.by_tier.get(clf.tier, 0) + 1
        result.n_picks_settled += 1

    if new_rows:
        store.upsert("nrfi_pick_settled", new_rows)
        _refresh_tier_ledger(store, season=season)

    result.n_picks_no_actual = (
        result.n_picks_examined
        - result.n_picks_already_settled
        - result.n_picks_settled
    )
    return result


def _refresh_tier_ledger(store: NRFIStore, *, season: int) -> None:
    """Re-aggregate `nrfi_tier_ledger` from the settled-picks table.

    Cheaper than doing it in-place per pick — settlement is rare
    (once a day) and the table is tiny (O(weeks × 2 markets × 5 tiers)).
    """
    # Per (market_type, tier).
    rows = store.query_df(
        """
        SELECT season, market_type, tier,
               COUNT(*)          AS n_settled,
               SUM(CASE WHEN actual_hit THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN actual_hit THEN 0 ELSE 1 END) AS losses,
               SUM(units_delta)  AS units_won
        FROM nrfi_pick_settled
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

    # Cross-tier roll-up per market.
    market_rollups = store.query_df(
        """
        SELECT season, market_type,
               COUNT(*)          AS n_settled,
               SUM(CASE WHEN actual_hit THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN actual_hit THEN 0 ELSE 1 END) AS losses,
               SUM(units_delta)  AS units_won
        FROM nrfi_pick_settled
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

    # Cross-market cross-tier total — useful for the email's headline.
    total = store.query_df(
        """
        SELECT season,
               COUNT(*)          AS n_settled,
               SUM(CASE WHEN actual_hit THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN actual_hit THEN 0 ELSE 1 END) AS losses,
               SUM(units_delta)  AS units_won
        FROM nrfi_pick_settled
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

    # Wipe + reinsert atomically (small table; correctness > cleverness).
    store.execute(
        "DELETE FROM nrfi_tier_ledger WHERE season = ?",
        (int(season),),
    )
    if by_tier_market:
        store.upsert("nrfi_tier_ledger", by_tier_market)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def get_tier_ledger(store: NRFIStore, season: int):
    """Return the ledger as a DataFrame ordered by tier (ELITE first).

    Empty DataFrame when nothing has settled yet for the season.
    """
    df = store.query_df(
        """
        SELECT season, market_type, tier,
               n_settled, wins, losses, units_won, last_updated
        FROM nrfi_tier_ledger
        WHERE season = ?
        """,
        (int(season),),
    )
    if df is None or df.empty:
        return df
    tier_order = {t.value: i for i, t in enumerate(
        [Tier.ELITE, Tier.STRONG, Tier.MODERATE, Tier.LEAN]
    )}
    tier_order["ALL"] = 99
    df["_tier_rank"] = df["tier"].map(lambda t: tier_order.get(t, 100))
    df["_market_rank"] = df["market_type"].map(
        lambda m: 0 if m == "NRFI" else (1 if m == "YRFI" else 2)
    )
    df = df.sort_values(["_market_rank", "_tier_rank"]).drop(
        columns=["_tier_rank", "_market_rank"]
    )
    return df.reset_index(drop=True)


def render_ledger_section(store: NRFIStore, season: int) -> str:
    """Plain-text YTD ledger block for the daily email / dashboard.

    Returns "" when nothing has settled yet (caller should skip the
    section entirely in that case)."""
    df = get_tier_ledger(store, season)
    if df is None or df.empty:
        return ""

    lines = [f"YTD LEDGER ({season})", "─" * 60]
    fmt = "  {market:<6} {tier:<9} {record:>10}  {units:>+8.2f}u  {roi:>+6.1f}%"
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
        description="NRFI per-tier YTD ledger — settle / show"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_settle = sub.add_parser("settle", help="Run a settlement pass")
    p_settle.add_argument("--season", type=int, default=date.today().year)
    p_settle.add_argument("--cutoff-date", default=None)
    p_settle.add_argument("--no-pull-actuals", action="store_true",
                            help="Skip backfill_actuals; assume already populated")

    p_show = sub.add_parser("show", help="Print the YTD ledger")
    p_show.add_argument("--season", type=int, default=date.today().year)

    args = parser.parse_args(list(argv) if argv is not None else None)
    cfg = get_default_config().resolve_paths()
    store = NRFIStore(cfg.duckdb_path)

    if args.cmd == "settle":
        result = settle_predictions(
            store,
            season=args.season,
            cutoff_date=args.cutoff_date,
            pull_actuals=not args.no_pull_actuals,
        )
        print(result.summary())
        return 0
    if args.cmd == "show":
        print(render_ledger_section(store, season=args.season) or
              f"(no settled picks for {args.season} yet)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
