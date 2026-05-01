"""Source adapters for the NRFI/YRFI engine.

The original engine modules still live under ``nrfi.data`` for compatibility.
New code can import source concerns from here while phase scripts continue to
use the established paths.
"""

from edge_equation.engines.nrfi.data.odds import capture_closing_lines
from edge_equation.engines.nrfi.data.scrapers_etl import daily_etl

__all__ = ["capture_closing_lines", "daily_etl"]
