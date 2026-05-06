"""Unified NCAAF daily runner.

Mirrors `engines.nfl.parlay_runner.build_unified_nfl_card`.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger

from .game_results_parlay import NCAAFGameResultsParlayEngine
from .player_props_parlay import NCAAFPlayerPropsParlayEngine
from .thresholds import (
    ALLOWED_NCAAF_GAME_RESULT_MARKETS,
    ALLOWED_NCAAF_PLAYER_PROP_MARKETS,
    NCAAF_PARLAY_RULES,
    NCAAFParlayRules,
)

log = get_logger(__name__)


@dataclass
class UnifiedNCAAFCard:
    target_date: str
    outputs: list = field(default_factory=list)
    game_results_parlay_card: Optional[object] = None
    player_props_parlay_card: Optional[object] = None

    @property
    def has_any_picks(self) -> bool:
        return any([
            bool(self.outputs),
            bool(
                self.game_results_parlay_card
                and getattr(
                    self.game_results_parlay_card, "has_qualified", False,
                )
            ),
            bool(
                self.player_props_parlay_card
                and getattr(
                    self.player_props_parlay_card, "has_qualified", False,
                )
            ),
        ])


def _run_ncaaf_engine(target_date: str) -> list:
    """Best-effort wrapper around `engines.ncaaf.daily.build_ncaaf_card`."""
    try:
        from .daily import build_ncaaf_card
        card = build_ncaaf_card(target_date)
        return list(getattr(card, "picks", []) or [])
    except Exception as e:
        log.warning(
            "NCAAF unified runner: per-row engine failed (%s): %s",
            type(e).__name__, e,
        )
        return []


def _split_outputs(outputs: Sequence) -> tuple[list, list]:
    game_rows: list = []
    prop_rows: list = []
    for o in outputs:
        market = str(getattr(o, "market_type", "") or "")
        if market in ALLOWED_NCAAF_GAME_RESULT_MARKETS:
            game_rows.append(o)
        elif market in ALLOWED_NCAAF_PLAYER_PROP_MARKETS:
            prop_rows.append(o)
    return game_rows, prop_rows


def build_unified_ncaaf_card(
    target_date: Optional[str] = None,
    *,
    rules: NCAAFParlayRules = NCAAF_PARLAY_RULES,
    top_n_parlays: int = 3,
) -> UnifiedNCAAFCard:
    target = target_date or _date.today().isoformat()
    outputs = _run_ncaaf_engine(target)
    game_rows, prop_rows = _split_outputs(outputs)

    game_card = NCAAFGameResultsParlayEngine(
        rules=rules, top_n=top_n_parlays,
    ).run(ncaaf_outputs=game_rows, target_date=target)

    props_card = NCAAFPlayerPropsParlayEngine(
        rules=rules, top_n=top_n_parlays,
    ).run(ncaaf_prop_outputs=prop_rows, target_date=target)

    return UnifiedNCAAFCard(
        target_date=target,
        outputs=outputs,
        game_results_parlay_card=game_card,
        player_props_parlay_card=props_card,
    )


@dataclass
class NCAAFDailyRunner:
    rules: NCAAFParlayRules = NCAAF_PARLAY_RULES
    top_n_parlays: int = 3
    name: str = "ncaaf_daily"

    def run(self, target_date: Optional[str] = None) -> UnifiedNCAAFCard:
        return build_unified_ncaaf_card(
            target_date,
            rules=self.rules,
            top_n_parlays=self.top_n_parlays,
        )


def _format_card(card: UnifiedNCAAFCard) -> str:
    parts: list[str] = [
        f"NCAAF DAILY CARD — {card.target_date}",
        "=" * 60,
        "",
    ]
    if card.outputs:
        parts.append(
            f"Per-row qualified outputs: {len(card.outputs)} (game + props)"
        )
        parts.append("")
    if card.game_results_parlay_card is not None:
        parts.append(getattr(
            card.game_results_parlay_card, "top_board_text", "",
        ))
    if card.player_props_parlay_card is not None:
        parts.append(getattr(
            card.player_props_parlay_card, "top_board_text", "",
        ))
    if not card.has_any_picks:
        parts.append(
            "  No picks for this slate — engines were run but produced "
            "no qualifying entries."
        )
    return "\n".join(parts).rstrip() + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the unified NCAAF daily card (game markets + props "
                    "+ both parlay engines).",
    )
    parser.add_argument(
        "--date", default=None,
        help="Slate date YYYY-MM-DD. Default: today (UTC).",
    )
    parser.add_argument(
        "--top-n-parlays", type=int, default=3,
        help="Maximum parlay candidates to surface per universe.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    card = build_unified_ncaaf_card(
        args.date,
        top_n_parlays=args.top_n_parlays,
    )
    print(_format_card(card))
    return 0


if __name__ == "__main__":
    sys.exit(main())
