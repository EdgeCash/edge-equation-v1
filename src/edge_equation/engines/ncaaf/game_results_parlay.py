"""NCAAF game-results parlay engine — strict 3–6 leg builder.

Thin façade over `engines.football_core.parlay_common`. Same shape as
`engines.nfl.game_results_parlay` but wired to the NCAAF rules.
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
    NCAAF_PARLAY_RULES,
    NCAAFParlayRules,
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
)

log = get_logger(__name__)


def build_game_results_legs(
    *,
    ncaaf_outputs: Sequence = (),
    rules: NCAAFParlayRules = NCAAF_PARLAY_RULES,
):
    return _build_legs_common(
        outputs=ncaaf_outputs, rules=rules, market_universe="game_results",
    )


def filter_legs_by_strict_rules(
    legs, *, rules: NCAAFParlayRules = NCAAF_PARLAY_RULES,
):
    return _filter_common(
        legs, rules=rules, market_universe="game_results",
    )


def build_game_results_parlay(
    *,
    ncaaf_outputs: Sequence = (),
    target_date: Optional[str] = None,
    rules: NCAAFParlayRules = NCAAF_PARLAY_RULES,
    top_n: int = 3,
) -> FootballParlayCard:
    return build_parlay_card(
        sport="ncaaf",
        universe="game_results",
        outputs=ncaaf_outputs,
        target_date=target_date,
        rules=rules,
        note=PARLAY_CARD_NOTE,
        transparency_note=PARLAY_TRANSPARENCY_NOTE,
        no_qualified_message=NO_QUALIFIED_PARLAY_MESSAGE,
        top_n=top_n,
    )


@dataclass
class NCAAFGameResultsParlayEngine:
    rules: NCAAFParlayRules = NCAAF_PARLAY_RULES
    top_n: int = 3
    name: str = "ncaaf_game_results_parlay"

    def run(
        self, *,
        ncaaf_outputs: Sequence = (),
        target_date: Optional[str] = None,
    ) -> FootballParlayCard:
        try:
            return build_game_results_parlay(
                ncaaf_outputs=ncaaf_outputs,
                target_date=target_date,
                rules=self.rules,
                top_n=self.top_n,
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                "NCAAFGameResultsParlayEngine: build failed (%s): %s",
                type(e).__name__, e,
            )
            return FootballParlayCard(
                target_date=target_date or _date.today().isoformat(),
                sport="ncaaf", universe="game_results",
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
        rules: NCAAFParlayRules = NCAAF_PARLAY_RULES,
    ) -> float:
        return joint_probability(legs, rules=rules)
