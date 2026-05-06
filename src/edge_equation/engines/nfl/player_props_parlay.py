"""NFL player-props parlay engine — strict 3–6 leg builder.

Thin façade over `engines.football_core.parlay_common`. Same shape as
`engines.nfl.game_results_parlay` — just toggles the universe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from typing import Optional, Sequence

from edge_equation.engines.football_core.parlay_common import (
    FootballParlayCard,
    build_legs as _build_legs_common,
    build_parlay_card,
    filter_legs_by_strict_rules as _filter_common,
    joint_probability,
)
from edge_equation.engines.parlay import ParlayLeg
from edge_equation.utils.logging import get_logger

from .thresholds import (
    NFL_PARLAY_RULES,
    NFLParlayRules,
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
)

log = get_logger(__name__)


def build_player_props_legs(
    *,
    nfl_prop_outputs: Sequence = (),
    rules: NFLParlayRules = NFL_PARLAY_RULES,
):
    return _build_legs_common(
        outputs=nfl_prop_outputs, rules=rules,
        market_universe="player_props",
    )


def filter_legs_by_strict_rules(
    legs, *, rules: NFLParlayRules = NFL_PARLAY_RULES,
):
    return _filter_common(
        legs, rules=rules, market_universe="player_props",
    )


def build_player_props_parlay(
    *,
    nfl_prop_outputs: Sequence = (),
    target_date: Optional[str] = None,
    rules: NFLParlayRules = NFL_PARLAY_RULES,
    top_n: int = 3,
) -> FootballParlayCard:
    return build_parlay_card(
        sport="nfl",
        universe="player_props",
        outputs=nfl_prop_outputs,
        target_date=target_date,
        rules=rules,
        note=PARLAY_CARD_NOTE,
        transparency_note=PARLAY_TRANSPARENCY_NOTE,
        no_qualified_message=NO_QUALIFIED_PARLAY_MESSAGE,
        top_n=top_n,
    )


@dataclass
class NFLPlayerPropsParlayEngine:
    rules: NFLParlayRules = NFL_PARLAY_RULES
    top_n: int = 3
    name: str = "nfl_player_props_parlay"

    def run(
        self, *,
        nfl_prop_outputs: Sequence = (),
        target_date: Optional[str] = None,
    ) -> FootballParlayCard:
        try:
            return build_player_props_parlay(
                nfl_prop_outputs=nfl_prop_outputs,
                target_date=target_date,
                rules=self.rules,
                top_n=self.top_n,
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                "NFLPlayerPropsParlayEngine: build failed (%s): %s",
                type(e).__name__, e,
            )
            return FootballParlayCard(
                target_date=target_date or _date.today().isoformat(),
                sport="nfl", universe="player_props",
                explanation=(
                    f"{NO_QUALIFIED_PARLAY_MESSAGE} (build error: "
                    f"{type(e).__name__})"
                ),
                note=PARLAY_CARD_NOTE,
                transparency_note=PARLAY_TRANSPARENCY_NOTE,
            )

    @staticmethod
    def joint_probability(
        legs: Sequence[ParlayLeg], *,
        rules: NFLParlayRules = NFL_PARLAY_RULES,
    ) -> float:
        return joint_probability(legs, rules=rules)
