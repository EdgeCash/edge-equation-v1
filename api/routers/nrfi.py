"""Elite NRFI/YRFI API router.

Exposes today's NRFI board (post-engine, post-calibration) for the
website / dashboard / Discord card. The route is read-only and does
not require auth — same as `/picks/today`.

Endpoints
---------
* `GET /nrfi/today` — list of per-game NRFI/YRFI predictions for the
  current MLB slate. Returns an empty list when the optional `[nrfi]`
  extras aren't installed (route stays mounted to keep the schema
  stable).

* `GET /nrfi/board?date=YYYY-MM-DD` — same shape, for an arbitrary
  date if the engine has predictions stored in DuckDB.

Both routes are intentionally tolerant of missing data: an empty list
is preferable to a 500 when the daily ETL hasn't yet run for the day.
"""

from __future__ import annotations

from datetime import date as _date
from typing import List, Optional

from fastapi import APIRouter, Query


router = APIRouter(prefix="/nrfi", tags=["nrfi"])


@router.get("/today")
def get_nrfi_today() -> List[dict]:
    """Return today's NRFI/YRFI board (one row per market per game)."""
    return _board_for(_date.today().isoformat())


@router.get("/board")
def get_nrfi_board(
    date: str = Query(..., description="YYYY-MM-DD"),
) -> List[dict]:
    return _board_for(date)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _board_for(target_date: str) -> List[dict]:
    """Pull stored predictions from DuckDB. Quietly returns [] when the
    NRFI subsystem is not installed (so the API shape stays stable)."""
    try:
        from edge_equation.engines.nrfi.config import get_default_config
        from edge_equation.engines.nrfi.data.storage import NRFIStore
    except ImportError:
        return []
    try:
        cfg = get_default_config()
        store = NRFIStore(cfg.duckdb_path)
        df = store.predictions_for_date(target_date)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    keep_cols = [c for c in [
        "game_pk", "home_team", "away_team", "first_pitch_ts",
        "nrfi_pct", "lambda_total", "color_band", "signal",
        "mc_low", "mc_high", "edge", "kelly_units", "shap_drivers",
    ] if c in df.columns]
    return df[keep_cols].to_dict(orient="records")
