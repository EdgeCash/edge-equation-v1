"""Unified NFL daily-board entry point — Phase F-1 stub.

Mirrors `edge_equation.run_daily` (the MLB unified entry) so the
operator can keep one mental model::

    python -m edge_equation.run_daily         # MLB engines
    python -m edge_equation.run_daily_nfl     # NFL (this entry, stubbed in F-1)
    python -m edge_equation.run_daily_ncaaf   # NCAAF (F-1 stub)

Phase F-1 prints the skeleton card's placeholder note. Once the
projection + source layers ship in F-2, this will delegate to a
real email-build path the same way MLB's `run_daily` delegates to
`engines/nrfi/email_report.main`.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Phase F-1 stub. Real workflow integration ships in F-5."""
    from edge_equation.engines.nfl.daily import main as nfl_main
    return nfl_main(argv)


if __name__ == "__main__":
    sys.exit(main())
