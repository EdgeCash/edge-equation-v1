"""Canonical output payload for MLB player props."""

from __future__ import annotations

from dataclasses import dataclass

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
    )


def render_prop_output(out: PropOutput) -> str:
    """Single-line professional display for future daily reports."""

    line = f" {out.line:g}" if out.line is not None else ""
    player = f"{out.player_name} " if out.player_name else ""
    return (
        f"{out.tier.value:<8} {player}{out.selection}{line} "
        f"model={out.model_prob*100:.1f}% market={out.market_prob*100:.1f}% "
        f"edge={out.edge_pp:+.1f}pp odds={out.american_odds:+.0f} "
        f"book={out.bookmaker}"
    )


__all__ = ["PropOutput", "build_prop_output", "render_prop_output"]
