"""Full-game canonical output payload + email/api adapters."""

from .payload import (
    FullGameOutput,
    build_full_game_output,
    color_band_for_tier,
    color_hex_for_tier,
    to_api_dict,
    to_email_card,
)

__all__ = [
    "FullGameOutput",
    "build_full_game_output",
    "color_band_for_tier",
    "color_hex_for_tier",
    "to_api_dict",
    "to_email_card",
]
