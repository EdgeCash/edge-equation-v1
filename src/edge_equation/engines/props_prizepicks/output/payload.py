"""Canonical output payload for MLB player props."""

from __future__ import annotations

from dataclasses import dataclass

from edge_equation.engines.core.posting.conviction import (
    conviction_band,
    format_conviction_line,
)
from edge_equation.engines.tiering import Tier

from ..models.projection import PropProjection


@dataclass(frozen=True)
class PropOutput:
    player_name: str
    market_key: str
    selection: str
    line: float | None
    american_odds: float
    market_prob: float
    model_prob: float
    edge: float
    tier: Tier
    bookmaker: str
    conviction_color: str
    conviction_hex: str

    @property
    def edge_pp(self) -> float:
        return self.edge * 100.0


def build_prop_output(projection: PropProjection) -> PropOutput:
    """Promote a projection result to the engine's output contract."""

    return PropOutput(
        player_name=projection.player_name,
        market_key=projection.market_key,
        selection=projection.selection,
        line=projection.line,
        american_odds=projection.american_odds,
        market_prob=projection.market_prob,
        model_prob=projection.model_prob,
        edge=projection.edge,
        tier=projection.tier,
        bookmaker=projection.bookmaker,
        conviction_color=conviction_band(
            projection.model_prob,
            edge=projection.edge,
            is_electric=projection.tier in (Tier.LOCK, Tier.STRONG),
        ).label,
        conviction_hex=conviction_band(
            projection.model_prob,
            edge=projection.edge,
            is_electric=projection.tier in (Tier.LOCK, Tier.STRONG),
        ).hex_color,
    )


def render_prop_output(out: PropOutput) -> str:
    """Single-line professional display for future daily reports."""

    line = f" {out.line:g}" if out.line is not None else ""
    player = f"{out.player_name} " if out.player_name else ""
    band = conviction_band(
        out.model_prob,
        edge=out.edge,
        is_electric=out.conviction_color == "Electric Blue",
    )
    label = f"{player}{out.selection}{line}".strip()
    return (
        format_conviction_line(label=label, model_probability=out.model_prob, band=band)
        + f"  edge={out.edge_pp:+.1f}pp odds={out.american_odds:+.0f} book={out.bookmaker}"
    )


__all__ = ["PropOutput", "build_prop_output", "render_prop_output"]
