"""Unified WNBA daily runner.

Mirrors `engines.mlb.run_daily.build_unified_mlb_card`. One pass
produces:

* The existing WNBA per-row outputs (game-results + player props),
  via `engines.wnba.run_daily.WNBARunner`.
* The new strict-policy game-results parlay card.
* The new strict-policy player-props parlay card.

The runner is best-effort end-to-end: a missing odds feed or a
single-engine error yields an empty card section, but never blocks
the rest of the pipeline. Operators see "Limited Data" badges in
the website feed instead of a 500.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger

from .game_results_parlay import (
    WNBAGameResultsParlayCard,
    WNBAGameResultsParlayEngine,
)
from .player_props_parlay import (
    WNBAPlayerPropsParlayCard,
    WNBAPlayerPropsParlayEngine,
)
from .schema import Market
from .thresholds import WNBA_PARLAY_RULES, WNBAParlayRules

log = get_logger(__name__)


GAME_MARKETS = {
    Market.FULLGAME_ML.value,
    Market.FULLGAME_SPREAD.value,
    Market.FULLGAME_TOTAL.value,
}


@dataclass
class UnifiedWNBACard:
    target_date: str
    outputs: list = field(default_factory=list)
    game_results_parlay_card: Optional[WNBAGameResultsParlayCard] = None
    player_props_parlay_card: Optional[WNBAPlayerPropsParlayCard] = None

    @property
    def has_any_picks(self) -> bool:
        return any([
            bool(self.outputs),
            bool(
                self.game_results_parlay_card
                and self.game_results_parlay_card.has_qualified
            ),
            bool(
                self.player_props_parlay_card
                and self.player_props_parlay_card.has_qualified
            ),
        ])


def _run_wnba_engine(target_date: str) -> list:
    """Best-effort wrapper around `engines.wnba.run_daily.WNBARunner`."""
    try:
        from .run_daily import WNBARunner
        runner = WNBARunner()
        return list(runner.run(target_date) or [])
    except Exception as e:
        log.warning(
            "WNBA unified runner: per-row engine failed (%s): %s",
            type(e).__name__, e,
        )
        return []


def _split_outputs(outputs: Sequence) -> tuple[list, list]:
    """Split the per-row outputs into (game_outputs, prop_outputs)."""
    game_rows: list = []
    prop_rows: list = []
    for o in outputs:
        market = getattr(o, "market", None)
        market_str = (
            market.value if hasattr(market, "value") else str(market or "")
        )
        if market_str in GAME_MARKETS:
            game_rows.append(o)
        else:
            prop_rows.append(o)
    return game_rows, prop_rows


def build_unified_wnba_card(
    target_date: Optional[str] = None,
    *,
    rules: WNBAParlayRules = WNBA_PARLAY_RULES,
    top_n_parlays: int = 3,
) -> UnifiedWNBACard:
    """Build the unified WNBA daily card."""
    target = target_date or _date.today().isoformat()
    outputs = _run_wnba_engine(target)
    game_rows, prop_rows = _split_outputs(outputs)

    game_card = WNBAGameResultsParlayEngine(
        rules=rules, top_n=top_n_parlays,
    ).run(wnba_outputs=game_rows, target_date=target)

    props_card = WNBAPlayerPropsParlayEngine(
        rules=rules, top_n=top_n_parlays,
    ).run(wnba_prop_outputs=prop_rows, target_date=target)

    return UnifiedWNBACard(
        target_date=target,
        outputs=outputs,
        game_results_parlay_card=game_card,
        player_props_parlay_card=props_card,
    )


@dataclass
class WNBADailyRunner:
    """Engine-registry façade for the unified WNBA daily runner."""

    rules: WNBAParlayRules = WNBA_PARLAY_RULES
    top_n_parlays: int = 3
    name: str = "wnba_daily"

    def run(self, target_date: Optional[str] = None) -> UnifiedWNBACard:
        return build_unified_wnba_card(
            target_date,
            rules=self.rules,
            top_n_parlays=self.top_n_parlays,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_card(card: UnifiedWNBACard) -> str:
    parts: list[str] = [
        f"WNBA DAILY CARD — {card.target_date}",
        "=" * 60,
        "",
    ]
    if card.outputs:
        parts.append(
            f"Per-row qualified outputs: {len(card.outputs)} (game + props)"
        )
        parts.append("")
    if card.game_results_parlay_card is not None:
        parts.append(card.game_results_parlay_card.top_board_text)
    if card.player_props_parlay_card is not None:
        parts.append(card.player_props_parlay_card.top_board_text)
    if not card.has_any_picks:
        parts.append(
            "  No picks for this slate — engines were run but produced "
            "no qualifying entries."
        )
    return "\n".join(parts).rstrip() + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the unified WNBA daily card (game markets + props "
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

    card = build_unified_wnba_card(
        args.date,
        top_n_parlays=args.top_n_parlays,
    )
    print(_format_card(card))
    return 0


if __name__ == "__main__":
    sys.exit(main())
