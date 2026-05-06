"""Unified NFL daily runner.

Mirrors `engines.wnba.parlay_runner.build_unified_wnba_card` and
`engines.mlb.run_daily.build_unified_mlb_card`. Produces:

* The existing NFL per-row outputs (game-results + player props),
  via `engines.nfl.daily.build_nfl_card`.
* The new strict-policy game-results parlay card.
* The new strict-policy player-props parlay card.

Best-effort throughout — a missing odds feed or per-engine error
yields an empty section but never blocks the rest of the pipeline.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger

from .game_results_parlay import NFLGameResultsParlayEngine
from .player_props_parlay import NFLPlayerPropsParlayEngine
from .thresholds import (
    ALLOWED_NFL_GAME_RESULT_MARKETS,
    ALLOWED_NFL_PLAYER_PROP_MARKETS,
    NFL_PARLAY_RULES,
    NFLParlayRules,
)

log = get_logger(__name__)


@dataclass
class UnifiedNFLCard:
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


def _run_nfl_engine(target_date: str) -> list:
    """Best-effort wrapper around `engines.nfl.daily.build_nfl_card`."""
    try:
        from .daily import build_nfl_card
        card = build_nfl_card(target_date)
        return list(getattr(card, "picks", []) or [])
    except Exception as e:
        log.warning(
            "NFL unified runner: per-row engine failed (%s): %s",
            type(e).__name__, e,
        )
        return []


def _split_outputs(outputs: Sequence) -> tuple[list, list]:
    """Split outputs into (game_outputs, prop_outputs)."""
    game_rows: list = []
    prop_rows: list = []
    for o in outputs:
        market = str(getattr(o, "market_type", "") or "")
        if market in ALLOWED_NFL_GAME_RESULT_MARKETS:
            game_rows.append(o)
        elif market in ALLOWED_NFL_PLAYER_PROP_MARKETS:
            prop_rows.append(o)
    return game_rows, prop_rows


def build_unified_nfl_card(
    target_date: Optional[str] = None,
    *,
    rules: NFLParlayRules = NFL_PARLAY_RULES,
    top_n_parlays: int = 3,
) -> UnifiedNFLCard:
    target = target_date or _date.today().isoformat()
    outputs = _run_nfl_engine(target)
    game_rows, prop_rows = _split_outputs(outputs)

    game_card = NFLGameResultsParlayEngine(
        rules=rules, top_n=top_n_parlays,
    ).run(nfl_outputs=game_rows, target_date=target)

    props_card = NFLPlayerPropsParlayEngine(
        rules=rules, top_n=top_n_parlays,
    ).run(nfl_prop_outputs=prop_rows, target_date=target)

    return UnifiedNFLCard(
        target_date=target,
        outputs=outputs,
        game_results_parlay_card=game_card,
        player_props_parlay_card=props_card,
    )


@dataclass
class NFLDailyRunner:
    rules: NFLParlayRules = NFL_PARLAY_RULES
    top_n_parlays: int = 3
    name: str = "nfl_daily"

    def run(self, target_date: Optional[str] = None) -> UnifiedNFLCard:
        return build_unified_nfl_card(
            target_date,
            rules=self.rules,
            top_n_parlays=self.top_n_parlays,
        )


def _format_card(card: UnifiedNFLCard) -> str:
    parts: list[str] = [
        f"NFL DAILY CARD — {card.target_date}",
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
        description="Run the unified NFL daily card (game markets + props "
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

    card = build_unified_nfl_card(
        args.date,
        top_n_parlays=args.top_n_parlays,
    )
    print(_format_card(card))
    return 0


if __name__ == "__main__":
    sys.exit(main())
