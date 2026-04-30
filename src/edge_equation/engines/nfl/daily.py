"""NFL daily orchestrator — skeleton.

When the engine is implemented this module will mirror
`engines/full_game/daily.py`:

1. Fetch this week's games from `source/odds_fetcher.py`.
2. Pull per-team rolling rates from `features/team_rates.py`.
3. Project each market via `models/projection.py`.
4. Compute vig-adjusted edges + tier-classify.
5. Build canonical `NFLOutput` rows.
6. Persist predictions into the NFL DuckDB.
7. Render the polished top-N-by-edge block matching the existing
   NRFI / Props / Full-Game TOP BOARD format.

Phase F-1 ships a stub `build_nfl_card` that returns an empty card
so the email body's NFL section renders an "engine not ready yet"
placeholder rather than a hard failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional, Sequence


@dataclass
class NFLCard:
    """Stub card — will mirror `FullGameCard` in F-2."""
    target_date: str = ""
    picks: list = field(default_factory=list)
    top_board_text: str = ""
    ledger_text: str = ""
    n_lines_fetched: int = 0
    n_qualifying_picks: int = 0
    notes: str = ""


def build_nfl_card(
    target_date: Optional[str] = None,
    *,
    config=None,
    http_client=None,
    api_key: Optional[str] = None,
    persist: bool = True,
    settle_yesterday: bool = True,
    top_n: int = 8,
) -> NFLCard:
    """Phase F-1 stub. Returns an empty card with a placeholder note.

    The full implementation lands in F-2 / F-3 once the source +
    projection modules are wired up. Keeping the function signature
    stable now so the future email integration just imports this
    name and calls it best-effort, same shape as NRFI / Props / FG.
    """
    target = target_date or _date.today().isoformat()
    return NFLCard(
        target_date=target,
        notes=(
            "NFL engine — Phase F-1 skeleton. Real predictions land "
            "after the projection + source layers ship in F-2."
        ),
    )


def render_top_nfl_block(picks: Sequence, *, n: int = 8) -> str:
    """Stub renderer — returns the empty string until F-2 ships."""
    return ""


# ---------------------------------------------------------------------------
# CLI entry — minimal stub
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Build today's NFL card (Phase F-1 stub)",
    )
    parser.add_argument("--date", default=None,
                          help="Slate date YYYY-MM-DD (default: today UTC).")
    args = parser.parse_args(list(argv) if argv is not None else None)
    card = build_nfl_card(args.date)
    print(f"NFL card for {card.target_date}")
    print(f"  notes: {card.notes}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
