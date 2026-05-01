"""Canonical output payload for the props engine.

One dataclass — `PropOutput` — that every consumer (dashboard, email,
API, posting card) reads. Mirrors NRFI's `NRFIOutput` shape so the
downstream renderers (top board, dashboard cards) can use the same
visual contract across engines.

Three adapters off the same payload:

* `to_email_card(out)`     — multi-line plain-text section.
* `to_api_dict(out)`       — JSON-serialisable dict for FastAPI.
* `build_prop_output(...)` — factory wires tier color + Kelly + drivers.

Construct via the factory rather than instantiating directly so the
color band, the Kelly stake, and the SHAP-style driver list stay
consistent with NRFI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from edge_equation.engines.tiering import (
    Tier, color_hex as tier_color_hex, kelly_multiplier,
)
from edge_equation.utils.kelly import american_to_decimal, kelly_stake

from ..edge import PropEdgePick


# ---------------------------------------------------------------------------
# Tier → color band labels — shared with NRFI / Full-Game vocabulary
# (Electric Blue / Deep Green / Light Green / Yellow / Orange).
# Props don't have NRFI-style "Strong YRFI = Red" treatment because the
# Over/Under dichotomy is symmetric (Over a HR line and Under a HR line
# both feel the same to the operator). Side-aware Red is NRFI/YRFI-only.
# ---------------------------------------------------------------------------

TIER_COLOR_BAND: dict[Tier, str] = {
    Tier.ELITE:    "Electric Blue",
    Tier.STRONG:   "Deep Green",
    Tier.MODERATE: "Light Green",
    Tier.LEAN:     "Yellow",
    Tier.NO_PLAY:  "Orange",
}


def color_band_for_tier(tier: Tier) -> str:
    """Plain-language band label for `tier`."""
    return TIER_COLOR_BAND[tier]


def color_hex_for_tier(tier: Tier) -> str:
    """Hex color for `tier` — passes through to the shared engines.tiering map."""
    return tier_color_hex(tier)


# ---------------------------------------------------------------------------
# PropOutput — the canonical payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PropOutput:
    """The single canonical props payload."""

    # Identity
    game_id: str
    market_type: str          # 'HR' / 'Hits' / 'Total_Bases' / 'RBI' / 'K'
    market_label: str         # 'Home Runs' / 'Hits' / etc.
    player_name: str
    line_value: float
    side: str                 # 'Over' / 'Under'

    # Probability
    model_prob: float
    model_pct: float          # 0..100, 1dp
    market_prob: float        # vig-adjusted implied book probability
    market_prob_raw: float    # raw vigged implied probability
    vig_corrected: bool       # True when devig pair was found

    # Volume / λ (audit trail)
    lam: float = 0.0
    blend_n: int = 0
    confidence: float = 0.30

    # MC band on the projection (5/95 percentile of bootstrapped probs).
    # ``mc_band_pp`` is the width in percentage points so the email layer
    # can render "stable" vs "fragile" projections at a glance.
    mc_low: float = 0.0
    mc_high: float = 0.0
    mc_band_pp: float = 0.0

    # Color
    color_band: str = "Yellow"
    color_hex: str = "#fbc02d"

    # Drivers (SHAP-style — populated by future Statcast feature contributions)
    driver_text: list[str] = field(default_factory=list)

    # Market & stake
    edge_pp: float = 0.0
    kelly_units: Optional[float] = None
    american_odds: float = -110.0
    decimal_odds: float = 1.91
    book: str = ""

    # Tier classification
    tier: str = "NO_PLAY"

    # Audit trail
    grade: str = "F"
    engine: str = "props_baseline"
    model_version: str = "props_v1"

    def headline(self) -> str:
        """One-liner: ``78.4% Over · Aaron Judge HR 0.5``."""
        return (
            f"{self.model_pct:.1f}% {self.side} · "
            f"{self.player_name} {self.market_label} {self.line_value}"
        )


# ---------------------------------------------------------------------------
# Factory — `PropEdgePick` (from edge.py) → `PropOutput`
# ---------------------------------------------------------------------------


def build_prop_output(
    pick: PropEdgePick, *,
    confidence: float = 0.30,
    lam: float = 0.0,
    blend_n: int = 0,
    driver_text: Optional[Sequence[str]] = None,
    mc_low: float = 0.0,
    mc_high: float = 0.0,
    mc_band_pp: float = 0.0,
    game_id: str = "",
    kelly_fraction: float = 0.25,
    min_edge: float = 0.02,
    vig_buffer: float = 0.01,
    max_stake_units: float = 2.0,
    grade: str = "C",
    engine: str = "props_baseline",
    model_version: str = "props_v1",
) -> PropOutput:
    """Build a canonical `PropOutput` from a `PropEdgePick`.

    Wires:
    * Color band + hex from the engine-wide TIER_COLOR_HEX (one
      visual brand across NRFI + props).
    * Kelly stake using the shared `kelly_stake` helper, scaled by
      the tier's recommended multiplier (ELITE 0.75x, STRONG 0.375x,
      MODERATE 0.175x, LEAN 0.0x).
    """
    tier_obj = pick.tier
    band = color_band_for_tier(tier_obj)
    hex_color = color_hex_for_tier(tier_obj)

    # Kelly stake — shared math; scaled by the tier multiplier so a
    # ELITE pick at 0.5u baseline becomes 0.5×0.75 = 0.375u, etc.
    rec = kelly_stake(
        model_prob=pick.model_prob,
        market_prob=pick.market_prob_devigged,
        american_odds=pick.american_odds,
        fraction=kelly_fraction,
        min_edge=min_edge,
        vig_buffer=vig_buffer,
        max_stake_units=max_stake_units,
    )
    multiplier = kelly_multiplier(tier_obj)
    kelly_units = (
        round(rec.stake_units * multiplier, 2) if rec.stake_units > 0 else 0.0
    )

    return PropOutput(
        game_id=str(game_id),
        market_type=pick.market_canonical,
        market_label=pick.market_label,
        player_name=pick.player_name,
        line_value=float(pick.line_value),
        side=str(pick.side),
        model_prob=float(pick.model_prob),
        model_pct=round(pick.model_prob * 100.0, 1),
        market_prob=float(pick.market_prob_devigged),
        market_prob_raw=float(pick.market_prob_raw),
        vig_corrected=bool(pick.vig_corrected),
        lam=float(lam),
        blend_n=int(blend_n),
        confidence=float(confidence),
        mc_low=float(mc_low),
        mc_high=float(mc_high),
        mc_band_pp=float(mc_band_pp),
        color_band=band,
        color_hex=hex_color,
        driver_text=list(driver_text or []),
        edge_pp=float(pick.edge_pp),
        kelly_units=kelly_units,
        american_odds=float(pick.american_odds),
        decimal_odds=float(pick.decimal_odds),
        book=str(pick.book),
        tier=tier_obj.value,
        grade=grade,
        engine=engine,
        model_version=model_version,
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def to_email_card(out: PropOutput) -> str:
    """Plain-text section formatted to match the NRFI top-board look.

    Layout::

        Aaron Judge · Home Runs Over 0.5                     [STRONG  ]
        24.3% Conviction · Deep Green · λ 0.28  conf 72%  edge +5.1pp  stake 0.50u
        odds +250  (DraftKings)
    """
    matchup = f"{out.player_name} · {out.market_label} {out.side} {out.line_value:g}"
    head = f"{matchup:<48}[{out.tier:<8}]".rstrip()
    metric_parts = [
        f"{out.model_pct:.1f}% Conviction",
        out.color_band,
        f"λ {out.lam:.2f}",
    ]
    metric_parts.append(f"conf {int(round(out.confidence * 100))}%")
    if out.edge_pp:
        sign = "+" if out.edge_pp >= 0 else ""
        metric_parts.append(f"edge {sign}{out.edge_pp:.1f}pp")
    if out.mc_band_pp > 0:
        metric_parts.append(f"band ±{out.mc_band_pp:.1f}pp")
    if out.kelly_units and out.kelly_units > 0:
        metric_parts.append(f"stake {out.kelly_units:.2f}u")
    metric_line = " · ".join(metric_parts[:3]) + "  " + "  ".join(
        metric_parts[3:],
    )
    odds_str = f"{out.american_odds:+.0f}" if out.american_odds > 0 else f"{out.american_odds:.0f}"
    odds_line = f"odds {odds_str}"
    if out.book:
        odds_line += f"  ({out.book})"
    lines = [head, metric_line, odds_line]
    if out.driver_text:
        lines.append("  Why: " + ", ".join(out.driver_text[:4]))
    return "\n".join(lines)


def to_api_dict(out: PropOutput) -> dict[str, Any]:
    """JSON-friendly dict for the dashboard / API."""
    d = asdict(out)
    d["headline"] = out.headline()
    return d
