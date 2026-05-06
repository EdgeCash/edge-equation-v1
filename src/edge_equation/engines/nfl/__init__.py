"""NFL engine — Phase F-1 skeleton.

Mirrors the MLB pattern (`engines/nrfi/`, `engines/props_prizepicks/`,
`engines/full_game/`) for visual + behavioral consistency. See
``README.md`` in this directory for the full architecture sketch and
the list of what's intentionally NOT shipped in F-1.
"""

from .config import NFLConfig, ProjectionKnobs, get_default_config
from .game_results_parlay import (
    NFLGameResultsParlayEngine,
    build_game_results_legs,
    build_game_results_parlay,
)
from .markets import MLB_FOOTBALL_TO_NFL, NFL_MARKETS
from .parlay_runner import (
    NFLDailyRunner,
    UnifiedNFLCard,
    build_unified_nfl_card,
)
from .player_props_parlay import (
    NFLPlayerPropsParlayEngine,
    build_player_props_legs,
    build_player_props_parlay,
)
from .thresholds import (
    NFL_PARLAY_RULES,
    NFLParlayRules,
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
)

__all__ = [
    # Existing surface
    "NFLConfig",
    "ProjectionKnobs",
    "get_default_config",
    "NFL_MARKETS",
    "MLB_FOOTBALL_TO_NFL",
    # Parlay surface
    "NFL_PARLAY_RULES",
    "NFLParlayRules",
    "PARLAY_CARD_NOTE",
    "PARLAY_TRANSPARENCY_NOTE",
    "NO_QUALIFIED_PARLAY_MESSAGE",
    "NFLGameResultsParlayEngine",
    "NFLPlayerPropsParlayEngine",
    "NFLDailyRunner",
    "UnifiedNFLCard",
    "build_game_results_parlay",
    "build_game_results_legs",
    "build_player_props_parlay",
    "build_player_props_legs",
    "build_unified_nfl_card",
]
