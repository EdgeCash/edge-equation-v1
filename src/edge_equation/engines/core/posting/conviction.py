"""Shared conviction display system for all engine outputs.

The engine math still owns probabilities, edges, Kelly, and tiers.  This module
only standardizes presentation language:

* always show raw model probability as "NN% Conviction";
* Electric Blue is reserved for the top ranked plays in a bet type;
* negative/bad edge is always Red when market edge is available;
* free posts can filter to Electric Blue only;
* premium boards can render the full color ladder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class ConvictionBand:
    label: str
    hex_color: str
    rank: int
    description: str


ELECTRIC_BLUE = ConvictionBand(
    "Electric Blue", "#00a3ff", 6, "Highest conviction play for this bet type",
)
DEEP_GREEN = ConvictionBand("Deep Green", "#1b5e20", 5, "Strong conviction")
LIGHT_GREEN = ConvictionBand("Light Green", "#7cb342", 4, "Moderate conviction")
YELLOW = ConvictionBand("Yellow", "#fbc02d", 3, "Lean")
ORANGE = ConvictionBand("Orange", "#ef6c00", 2, "Low conviction")
RED = ConvictionBand("Red", "#b00020", 1, "Avoid / very low conviction")


CONVICTION_KEY: tuple[ConvictionBand, ...] = (
    ELECTRIC_BLUE,
    DEEP_GREEN,
    LIGHT_GREEN,
    YELLOW,
    ORANGE,
    RED,
)


def conviction_band(
    model_probability: float,
    *,
    edge: Optional[float] = None,
    is_electric: bool = False,
) -> ConvictionBand:
    """Return the display band for a model probability.

    ``edge`` is decimal edge (model minus market).  When a market exists and
    edge is negative, the play is Red regardless of raw probability.
    """

    if edge is not None and float(edge) < 0:
        return RED
    if is_electric:
        return ELECTRIC_BLUE

    p = max(0.0, min(1.0, float(model_probability)))
    if p >= 0.70:
        return DEEP_GREEN
    if p >= 0.55:
        return LIGHT_GREEN
    if p >= 0.50:
        return YELLOW
    if p >= 0.45:
        return ORANGE
    return RED


def electric_indices(
    rows: Sequence[dict],
    *,
    top_n: int = 3,
    min_probability: float = 0.58,
) -> set[int]:
    """Return row indexes that should receive Electric Blue treatment.

    Rows sort by edge when edge is present, otherwise by raw model probability.
    A probability floor prevents weak slates from forcing a public play.
    """

    ranked: list[tuple[float, int]] = []
    for idx, row in enumerate(rows):
        p = float(row.get("model_probability", row.get("nrfi_prob", 0.0)) or 0.0)
        if p < min_probability:
            continue
        edge = row.get("edge")
        score = float(edge) if edge is not None else p
        ranked.append((score, idx))
    ranked.sort(reverse=True)
    return {idx for _, idx in ranked[:top_n]}


def conviction_text(model_probability: float) -> str:
    """Render raw model probability as the public conviction percentage."""

    return f"{float(model_probability) * 100.0:.0f}% Conviction"


def format_conviction_line(
    *,
    label: str,
    model_probability: float,
    band: ConvictionBand,
    stake_units: Optional[float] = None,
) -> str:
    """Clean one-line display used by free and premium surfaces."""

    stake = f"{float(stake_units):.1f}u" if stake_units is not None else ""
    return (
        f"{label:<32}  {conviction_text(model_probability)} "
        f"· {band.label:<13}  {stake}"
    ).rstrip()


def render_conviction_key() -> str:
    """Human-readable legend for reports and runbooks."""

    lines = ["Conviction Key", "-" * 56]
    for band in CONVICTION_KEY:
        lines.append(f"  {band.label:<13} {band.description}")
    return "\n".join(lines)


def filter_electric_blue(rows: Iterable[dict]) -> list[dict]:
    """Return rows already annotated as Electric Blue."""

    return [dict(r) for r in rows if r.get("conviction_color") == ELECTRIC_BLUE.label]


__all__ = [
    "CONVICTION_KEY",
    "DEEP_GREEN",
    "ELECTRIC_BLUE",
    "LIGHT_GREEN",
    "ORANGE",
    "RED",
    "YELLOW",
    "ConvictionBand",
    "conviction_band",
    "conviction_text",
    "electric_indices",
    "filter_electric_blue",
    "format_conviction_line",
    "render_conviction_key",
]
