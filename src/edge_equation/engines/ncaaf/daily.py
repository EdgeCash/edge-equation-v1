"""NCAAF daily orchestrator — skeleton.

Mirrors the NFL `daily.py` pattern. Phase F-1 returns an empty card
with a placeholder note so the email body's NCAAF section renders
"engine not ready yet" rather than crashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional, Sequence


@dataclass
class NCAAFCard:
    target_date: str = ""
    picks: list = field(default_factory=list)
    top_board_text: str = ""
    ledger_text: str = ""
    n_lines_fetched: int = 0
    n_qualifying_picks: int = 0
    notes: str = ""


def build_ncaaf_card(
    target_date: Optional[str] = None,
    *,
    config=None,
    http_client=None,
    api_key: Optional[str] = None,
    persist: bool = True,
    settle_yesterday: bool = True,
    top_n: int = 8,
) -> NCAAFCard:
    """Phase F-1 stub. Returns an empty card with a placeholder note."""
    target = target_date or _date.today().isoformat()
    return NCAAFCard(
        target_date=target,
        notes=(
            "NCAAF engine — Phase F-1 skeleton. Real predictions land "
            "after the projection + source layers ship in F-2."
        ),
    )


def render_top_ncaaf_block(picks: Sequence, *, n: int = 8) -> str:
    """Stub — empty until F-2."""
    return ""


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Build today's NCAAF card (Phase F-1 stub)",
    )
    parser.add_argument("--date", default=None,
                          help="Slate date YYYY-MM-DD (default: today UTC).")
    args = parser.parse_args(list(argv) if argv is not None else None)
    card = build_ncaaf_card(args.date)
    print(f"NCAAF card for {card.target_date}")
    print(f"  notes: {card.notes}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
