"""Props canonical output payload + email/api/dashboard adapters."""

from .payload import (
    PropOutput,
    build_prop_output,
    color_band_for_tier,
    color_hex_for_tier,
    to_api_dict,
    to_email_card,
)

__all__ = [
    "PropOutput",
    "build_prop_output",
    "color_band_for_tier",
    "color_hex_for_tier",
    "to_api_dict",
    "to_email_card",
]
