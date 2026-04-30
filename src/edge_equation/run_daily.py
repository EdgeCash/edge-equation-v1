"""Unified daily-board entry point.

Single command that runs the full pipeline:

* NRFI / YRFI engine — schedule pull, feature reconstruction, ML
  inference, tier classification, ledger settle, parlay candidates.
* Props engine — Odds API fetch, per-player projection, edge
  classification, ledger settle.
* One email body containing both engines' top boards + ledgers.

The actual work happens inside `nrfi.email_report.build_card`, which
already invokes the props orchestrator for its `props_top_text` /
`props_ledger_text` slots. This module is a thin façade so the cron
workflow (and the operator's manual runs) can hit a single command::

    python -m edge_equation.run_daily               # send email
    python -m edge_equation.run_daily --dry-run     # print only
    python -m edge_equation.run_daily --date 2026-04-29

That keeps the operator's mental model simple — "one command runs
the daily board" — while leaving each engine's CLI in place for
focused testing / inspection.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Delegate to nrfi.email_report.main — the daily-card entry that
    already orchestrates NRFI + props integration."""
    from edge_equation.engines.nrfi.email_report import main as nrfi_main
    return nrfi_main(argv)


if __name__ == "__main__":
    sys.exit(main())
