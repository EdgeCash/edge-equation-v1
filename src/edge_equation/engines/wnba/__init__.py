"""WNBA engine namespace.

Exposes the existing per-row WNBA runner alongside the new strict
parlay engines + unified daily runner, mirroring the MLB layout.

The strict-policy parlay engines are feature-flagged via the
engine registry (off by default until opening-weekend testing
clears) so importing this package never accidentally enables
production publication of WNBA parlays.
"""

from __future__ import annotations

from .game_results_parlay import (
    WNBAGameResultsParlayCard,
    WNBAGameResultsParlayEngine,
    build_game_results_legs,
    build_game_results_parlay,
)
from .parlay_runner import (
    UnifiedWNBACard,
    WNBADailyRunner,
    build_unified_wnba_card,
)
from .player_props_parlay import (
    WNBAPlayerPropsParlayCard,
    WNBAPlayerPropsParlayEngine,
    build_player_props_legs,
    build_player_props_parlay,
)
from .thresholds import (
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
    WNBA_PARLAY_RULES,
    WNBAParlayRules,
)

__all__ = [
    "WNBA_PARLAY_RULES",
    "WNBAParlayRules",
    "PARLAY_CARD_NOTE",
    "PARLAY_TRANSPARENCY_NOTE",
    "NO_QUALIFIED_PARLAY_MESSAGE",
    "WNBAGameResultsParlayCard",
    "WNBAGameResultsParlayEngine",
    "WNBAPlayerPropsParlayCard",
    "WNBAPlayerPropsParlayEngine",
    "WNBADailyRunner",
    "UnifiedWNBACard",
    "build_game_results_parlay",
    "build_game_results_legs",
    "build_player_props_parlay",
    "build_player_props_legs",
    "build_unified_wnba_card",
]
