"""NFL per-tier YTD ledger — skeleton.

Mirrors `engines/full_game/ledger.py`. Tables:

* ``nfl_pick_settled`` — PK ``(game_pk, market_type, side, line_value)``
* ``nfl_tier_ledger`` — PK ``(season, market_type, tier)`` with 'ALL'
  rollups.

Independent ledger from the MLB engines' — football variance differs
enough that combining ROI numbers would dilute the per-tier signal.
NCAAF ledger lives separately too (different variance from NFL).

Phase F-1 ships only the DDL strings + table-init helper. Settlement
+ render lands in F-2 once predictions are actually being written.
"""

from __future__ import annotations


_DDL_PICK_SETTLED = """
CREATE TABLE IF NOT EXISTS nfl_pick_settled (
    game_pk        BIGINT,
    market_type    VARCHAR,
    side           VARCHAR,
    team_tricode   VARCHAR,
    line_value     DOUBLE,
    season         INTEGER,
    week           INTEGER,
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
CREATE TABLE IF NOT EXISTS nfl_tier_ledger (
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


def init_ledger_tables(store) -> None:
    """Idempotent — safe to call on every settlement run.

    Phase F-1: just creates the empty tables so other parts of the
    engine can reference them. Settle pass + refresh land in F-2.
    """
    store.execute(_DDL_PICK_SETTLED)
    store.execute(_DDL_TIER_LEDGER)
