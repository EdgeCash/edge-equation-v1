"""Football data layer — DuckDB persistence + resumable backfill orchestrators.

Shared between `engines/nfl/` and `engines/ncaaf/`. Both leagues hit the
same DuckDB tables (with a `sport` discriminator column) so cross-sport
queries stay simple. The schema is designed to absorb whatever sources
we wire up — nflverse, College Football Data API, The Odds API, Open-
Meteo — without a migration when we add a new feature column later.

Modules
~~~~~~~

* `storage.py`         — DuckDB connection wrapper + DDL.
* `checkpoints.py`     — resumable chunking; track which (date, op)
                          pairs are already complete.
* `nflverse_loader.py` — NFL games + plays from nflverse parquet feeds.
* `cfbd_loader.py`     — NCAAF games from the College Football Data API.
* `weather_history.py` — Open-Meteo archive endpoint (per-venue lookup).
* `odds_history.py`    — The Odds API historical lines (paid tier; gated
                          behind a flag).
* `backfill_nfl.py`    — orchestrator stitching NFL loaders + storage.
* `backfill_ncaaf.py`  — orchestrator for NCAAF.
* `diagnostics.py`     — corpus size + missing-rate report CLI.
"""

from .storage import FootballStore, init_schema
from .checkpoints import (
    CheckpointResult,
    chunk_dates,
    completed_pairs,
    record_completion,
    record_failure,
)

__all__ = [
    "FootballStore",
    "init_schema",
    "CheckpointResult",
    "chunk_dates",
    "completed_pairs",
    "record_completion",
    "record_failure",
]
