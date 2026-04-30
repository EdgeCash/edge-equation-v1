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
from .markets import NCAAF_MARKETS

__all__ = [
    "NCAAFConfig",
    "ProjectionKnobs",
    "get_default_config",
    "NCAAF_MARKETS",
]
