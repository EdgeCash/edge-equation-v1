"""NCAAF per-tier YTD ledger — skeleton.

Mirrors `nfl/ledger.py`. Tables:

* ``ncaaf_pick_settled`` — PK ``(game_pk, market_type, side, line_value)``
* ``ncaaf_tier_ledger`` — PK ``(season, market_type, tier)`` with 'ALL'
  rollups.

Independent ledger from the NFL engine's — variance differs and
the per-tier ROI signals would dilute if combined.

Phase F-1 ships only the DDL strings. Settle pass + render in F-2.
"""

from __future__ import annotations


_DDL_PICK_SETTLED = """
CREATE TABLE IF NOT EXISTS ncaaf_pick_settled (
    game_pk        BIGINT,
    market_type    VARCHAR,
    side           VARCHAR,
    team_tricode   VARCHAR,
    line_value     DOUBLE,
    season         INTEGER,
    week           INTEGER,
    conference_tier_home VARCHAR,
    conference_tier_away VARCHAR,
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
CREATE TABLE IF NOT EXISTS ncaaf_tier_ledger (
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

    Phase F-1: just creates the empty tables. Settle + refresh in F-2.
    """
    store.execute(_DDL_PICK_SETTLED)
    store.execute(_DDL_TIER_LEDGER)
