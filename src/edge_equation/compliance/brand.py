"""
Edge Equation brand constants. Single source of truth for taglines and
brand-mandatory strings that the compliance checker enforces and the
exporters stamp into outputs.
"""
from __future__ import annotations

BRAND_TAGLINE: str = "Facts. Not Feelings."
BRAND_FULL: str = "Edge Equation — Facts. Not Feelings."


REQUIRED_TAGLINE_CONTEXTS: tuple[str, ...] = (
    "premium_daily",
    "spotlight",
    "ledger_recap",
    "mlb_workbook",
)
