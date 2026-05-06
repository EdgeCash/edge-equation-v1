"""NCAAF engine — Phase F-1 skeleton.

College football has a wider talent gap and looser markets than NFL,
which both help and hurt: bigger blowouts make spread distributions
fat-tailed, but small-conference matchups stay mispriced longer
because books focus their sharp lines on the marquee games.

See ``README.md`` in this directory for the full architecture sketch
and the list of NCAAF-specific challenges (recruit ratings, transfer
portal effects, conference tier handling).
"""

from .config import NCAAFConfig, ProjectionKnobs, get_default_config
from .game_results_parlay import (
    NCAAFGameResultsParlayEngine,
    build_game_results_legs,
    build_game_results_parlay,
)
from .markets import NCAAF_MARKETS
from .parlay_runner import (
    NCAAFDailyRunner,
    UnifiedNCAAFCard,
    build_unified_ncaaf_card,
)
from .player_props_parlay import (
    NCAAFPlayerPropsParlayEngine,
    build_player_props_legs,
    build_player_props_parlay,
)
from .thresholds import (
    NCAAF_PARLAY_RULES,
    NCAAFParlayRules,
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
)

__all__ = [
    # Existing surface
    "NCAAFConfig",
    "ProjectionKnobs",
    "get_default_config",
    "NCAAF_MARKETS",
    # Parlay surface
    "NCAAF_PARLAY_RULES",
    "NCAAFParlayRules",
    "PARLAY_CARD_NOTE",
    "PARLAY_TRANSPARENCY_NOTE",
    "NO_QUALIFIED_PARLAY_MESSAGE",
    "NCAAFGameResultsParlayEngine",
    "NCAAFPlayerPropsParlayEngine",
    "NCAAFDailyRunner",
    "UnifiedNCAAFCard",
    "build_game_results_parlay",
    "build_game_results_legs",
    "build_player_props_parlay",
    "build_player_props_legs",
    "build_unified_ncaaf_card",
]
