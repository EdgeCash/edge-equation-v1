"""Unified NCAAF daily-board entry point — Phase F-1 stub.

Mirrors `edge_equation.run_daily` (MLB) and `edge_equation.run_daily_nfl`.
Phase F-1 prints the skeleton card's placeholder note; real
workflow integration in F-5.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Phase F-1 stub. Real workflow integration ships in F-5."""
    from edge_equation.engines.ncaaf.daily import main as ncaaf_main
    return ncaaf_main(argv)


if __name__ == "__main__":
    sys.exit(main())
