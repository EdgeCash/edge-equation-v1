"""Backward-compatible import path for the shared Odds API client."""

from edge_equation.engines.core.data.odds_api_client import (  # noqa: F401
    API_KEY_ENV_VAR,
    DEFAULT_ENDPOINT,
    DEFAULT_ODDS_FORMAT,
    DEFAULT_REGIONS,
    DEFAULT_TTL_SECONDS,
    TheOddsApiClient,
)

__all__ = [
    "API_KEY_ENV_VAR",
    "DEFAULT_ENDPOINT",
    "DEFAULT_ODDS_FORMAT",
    "DEFAULT_REGIONS",
    "DEFAULT_TTL_SECONDS",
    "TheOddsApiClient",
]
