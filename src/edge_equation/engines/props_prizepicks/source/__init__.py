"""Source adapters for MLB props.

Despite the package name, live prop data should come from The Odds API.  The
historical PrizePicks CSV/scraper path is retained only as legacy data.
"""

from .odds_api import (
    MLB_PROPS_MARKETS,
    PropMarketQuote,
    PropsOddsApiSource,
    normalize_prop_quotes,
)

__all__ = [
    "MLB_PROPS_MARKETS",
    "PropMarketQuote",
    "PropsOddsApiSource",
    "normalize_prop_quotes",
]
