"""NFL engine — Phase F-1 skeleton.

Mirrors the MLB pattern (`engines/nrfi/`, `engines/props_prizepicks/`,
`engines/full_game/`) for visual + behavioral consistency. See
``README.md`` in this directory for the full architecture sketch and
the list of what's intentionally NOT shipped in F-1.
"""

from .config import NFLConfig, ProjectionKnobs, get_default_config
from .markets import MLB_FOOTBALL_TO_NFL, NFL_MARKETS

__all__ = [
    "NFLConfig",
    "ProjectionKnobs",
    "get_default_config",
    "NFL_MARKETS",
    "MLB_FOOTBALL_TO_NFL",
]
