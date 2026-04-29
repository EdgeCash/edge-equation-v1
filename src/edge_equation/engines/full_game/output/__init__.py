"""Full-game output renderers.

Skeleton package for MLB moneyline, total, first-five, and run-line outputs.
Uses the shared conviction vocabulary so future full-game model rows match
NRFI and props presentation from day one.
"""

from dataclasses import dataclass

from edge_equation.engines.core.posting.conviction import (
    conviction_band,
    format_conviction_line,
)


@dataclass(frozen=True)
class FullGameOutput:
    label: str
    model_probability: float
    edge: float | None = None
    stake_units: float | None = None


def render_full_game_output(out: FullGameOutput) -> str:
    band = conviction_band(out.model_probability, edge=out.edge)
    return format_conviction_line(
        label=out.label,
        model_probability=out.model_probability,
        band=band,
        stake_units=out.stake_units,
    )


__all__ = ["FullGameOutput", "render_full_game_output"]
