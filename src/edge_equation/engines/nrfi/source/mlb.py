"""NRFI source-layer compatibility exports.

The existing NRFI implementation keeps mature MLB Stats API ETL modules under
``nrfi.data``.  This package gives the engine the standard ``source`` boundary
used by the newer engine layout without forcing a risky internal move.
"""

from edge_equation.engines.nrfi.data.odds import capture_closing_lines
from edge_equation.engines.nrfi.data.scrapers_etl import backfill_actuals, daily_etl

__all__ = ["backfill_actuals", "capture_closing_lines", "daily_etl"]
