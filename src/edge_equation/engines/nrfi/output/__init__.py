"""Canonical output payload for NRFI/YRFI predictions.

Use this module — not `models.inference.Prediction` directly — when
serialising for the dashboard, email, or API. The `NRFIOutput`
dataclass is the contract every consumer reads, with one factory
(`build_output`) and three adapters (`to_email_card`, `to_api_dict`,
`to_dashboard_row`).
"""

from .payload import (
    NRFIOutput,
    build_output,
    to_api_dict,
    to_dashboard_row,
    to_email_card,
    to_jsonl,
)
from ..utils.colors import gradient_hex, nrfi_band, ColorBand  # convenience re-export

__all__ = [
    "NRFIOutput",
    "build_output",
    "to_api_dict",
    "to_dashboard_row",
    "to_email_card",
    "to_jsonl",
    "gradient_hex",
    "nrfi_band",
    "ColorBand",
]
