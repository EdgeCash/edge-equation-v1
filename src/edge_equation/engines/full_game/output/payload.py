"""Canonical output payload for the full-game engine.

Mirrors the NRFI/Props payload pattern so the email TOP BOARD format
reads identically across all three engines. Three adapters off one
dataclass:

* `to_email_card(out)`        — multi-line plain text block.
* `to_api_dict(out)`          — JSON-friendly dict for FastAPI.
* `build_full_game_output(...)` — factory wires color band, Kelly
                                     stake, driver text consistently.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from edge_equation.engines.tiering import (
    Tier,
    color_hex_for_pick,
    color_band_label_for_pick,
    kelly_multiplier,
)
from edge_equation.utils.kelly import kelly_stake

from ..edge import FullGameEdgePick


# ---------------------------------------------------------------------------
# Tier → color band labels (shared with NRFI / Props vocabulary). For
# side-aware rendering (Strong YRFI → Red), callers should hit the
# engines.tiering helpers `color_hex_for_pick(tier, market)` and
# `color_band_label_for_pick(tier, market)` directly. The map below is
# the default no-side fallback.
# ---------------------------------------------------------------------------

TIER_COLOR_BAND: dict[Tier, str] = {
    Tier.ELITE:    "Electric Blue",
    Tier.STRONG:   "Deep Green",
    Tier.MODERATE: "Light Green",
    Tier.LEAN:     "Yellow",
    Tier.NO_PLAY:  "Orange",
}


def color_band_for_tier(tier: Tier) -> str:
    """Default no-side band label for `tier`."""
    return TIER_COLOR_BAND[tier]


def color_hex_for_tier(tier: Tier) -> str:
    """Default no-side hex for `tier`. Side-aware callers should hit
    `color_hex_for_pick(tier, market)` instead."""
    from edge_equation.engines.tiering import TIER_COLOR_HEX
    return TIER_COLOR_HEX[tier]


# ---------------------------------------------------------------------------
# FullGameOutput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FullGameOutput:
    """The canonical full-game payload."""

    # Identity
    event_id: str
    market_type: str               # 'ML' / 'Run_Line' / 'Total' / ...
    market_label: str              # 'Moneyline' / 'Run Line' / 'Total Runs' / ...
    home_team: str
    away_team: str
    home_tricode: str
    away_tricode: str
    side: str                      # 'Over' / 'Under' / tricode
    team_tricode: str              # for team-side markets
    line_value: Optional[float]    # spread/total; None for ML

    # Probability
    model_prob: float
    model_pct: float               # 0..100, 1dp
    market_prob: float             # vig-adjusted implied
    market_prob_raw: float
    vig_corrected: bool

    # λ + audit
    lam_home: float = 0.0
    lam_away: float = 0.0
    lam_used: float = 0.0
    blend_n_home: int = 0
    blend_n_away: int = 0
    confidence: float = 0.30

    # MC band on the projection (5/95 percentile of bootstrapped probs).
    # ``mc_band_pp`` is the width in percentage points so the email
    # layer can render "stable" vs "fragile" projections at a glance.
    mc_low: float = 0.0
    mc_high: float = 0.0
    mc_band_pp: float = 0.0

    # Color
    color_band: str = "Yellow"
    color_hex: str = "#fbc02d"

    # Drivers (SHAP-style — populated by future per-feature contribution layer)
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
    engine: str = "fullgame_baseline"
    model_version: str = "fullgame_v1"

    # Upstream Odds-API ``commence_time`` (ISO 8601). Persisted in
    # fullgame_predictions and threaded into the daily-feed loader so
    # FeedPick.event_time is non-null for the upcoming-only failsafe.
    commence_time: str = ""

    def matchup(self) -> str:
        """`AWY @ HOM` short form for the email row."""
        if self.away_tricode and self.home_tricode:
            return f"{self.away_tricode} @ {self.home_tricode}"
        return f"{self.away_team} @ {self.home_team}"

    def headline(self) -> str:
        """Operator-readable selection: `NYY -1.5` / `Over 8.5` / `NYY ML`."""
        if self.market_type in ("ML", "F5_ML"):
            return f"{self.team_tricode or self.side} ML"
        if self.market_type == "Run_Line":
            # Spread lines need the explicit sign: -1.5 (favorite),
            # +1.5 (dog).
            line_str = (
                "" if self.line_value is None
                else f" {self.line_value:+g}"
            )
            return f"{self.team_tricode or self.side}{line_str}"
        if self.market_type in ("Total", "F5_Total", "Team_Total"):
            # Totals are inherently positive — render without sign.
            line_str = (
                "" if self.line_value is None else f" {self.line_value:g}"
            )
            return f"{self.side}{line_str}"
        return self.side


# ---------------------------------------------------------------------------
# Factory: FullGameEdgePick → FullGameOutput
# ---------------------------------------------------------------------------


def build_full_game_output(
    pick: FullGameEdgePick, *,
    confidence: float = 0.30,
    lam_home: float = 0.0,
    lam_away: float = 0.0,
    lam_used: float = 0.0,
    blend_n_home: int = 0,
    blend_n_away: int = 0,
    driver_text: Optional[Sequence[str]] = None,
    mc_low: float = 0.0,
    mc_high: float = 0.0,
    mc_band_pp: float = 0.0,
    event_id: str = "",
    kelly_fraction: float = 0.25,
    min_edge: float = 0.02,
    vig_buffer: float = 0.01,
    max_stake_units: float = 2.0,
    grade: str = "C",
    engine: str = "fullgame_baseline",
    model_version: str = "fullgame_v1",
) -> FullGameOutput:
    """Build a canonical `FullGameOutput` from a `FullGameEdgePick`."""
    tier_obj = pick.tier
    band = color_band_for_tier(tier_obj)
    hex_color = color_hex_for_tier(tier_obj)

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

    return FullGameOutput(
        event_id=str(event_id),
        market_type=pick.market_canonical,
        market_label=pick.market_label,
        home_team=pick.home_team, away_team=pick.away_team,
        home_tricode=pick.home_tricode, away_tricode=pick.away_tricode,
        side=str(pick.side), team_tricode=str(pick.team_tricode),
        line_value=pick.line_value,
        model_prob=float(pick.model_prob),
        model_pct=round(pick.model_prob * 100.0, 1),
        market_prob=float(pick.market_prob_devigged),
        market_prob_raw=float(pick.market_prob_raw),
        vig_corrected=bool(pick.vig_corrected),
        lam_home=float(lam_home), lam_away=float(lam_away),
        lam_used=float(lam_used),
        blend_n_home=int(blend_n_home), blend_n_away=int(blend_n_away),
        confidence=float(confidence),
        mc_low=float(mc_low), mc_high=float(mc_high),
        mc_band_pp=float(mc_band_pp),
        color_band=band, color_hex=hex_color,
        driver_text=list(driver_text or []),
        edge_pp=float(pick.edge_pp),
        kelly_units=kelly_units,
        american_odds=float(pick.american_odds),
        decimal_odds=float(pick.decimal_odds), book=str(pick.book),
        tier=tier_obj.value,
        grade=grade, engine=engine, model_version=model_version,
        commence_time=str(getattr(pick, "commence_time", "") or ""),
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def to_email_card(out: FullGameOutput) -> str:
    """Plain-text rendering matching the NRFI / Props TOP BOARD format.

    Layout::

        NYY @ BOS · Total Over 8.5                          [STRONG  ]
        61.3% Conviction · Deep Green · λ 9.54  conf 72%  edge +5.5pp  stake 0.50u
        odds -110  (draftkings)
          Why: + offense matchup, − BOS ace last 5
    """
    headline = f"{out.matchup()} · {out.market_label} {out.headline()}"
    head = f"{headline:<48}[{out.tier:<8}]".rstrip()

    metric_parts = [
        f"{out.model_pct:.1f}% Conviction",
        out.color_band,
        f"λ {out.lam_used:.2f}",
    ]
    extras = [f"conf {int(round(out.confidence * 100))}%"]
    if out.edge_pp:
        sign = "+" if out.edge_pp >= 0 else ""
        extras.append(f"edge {sign}{out.edge_pp:.1f}pp")
    if out.mc_band_pp > 0:
        extras.append(f"band ±{out.mc_band_pp:.1f}pp")
    if out.kelly_units and out.kelly_units > 0:
        extras.append(f"stake {out.kelly_units:.2f}u")
    metric_line = " · ".join(metric_parts) + "  " + "  ".join(extras)

    odds_str = (
        f"{out.american_odds:+.0f}" if out.american_odds > 0
        else f"{out.american_odds:.0f}"
    )
    odds_line = f"odds {odds_str}"
    if out.book:
        odds_line += f"  ({out.book})"

    lines = [head, metric_line, odds_line]
    if out.driver_text:
        lines.append("  Why: " + ", ".join(out.driver_text[:4]))
    return "\n".join(lines)


def to_api_dict(out: FullGameOutput) -> dict[str, Any]:
    d = asdict(out)
    d["headline"] = out.headline()
    d["matchup"] = out.matchup()
    return d
