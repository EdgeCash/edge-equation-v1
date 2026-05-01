"""Tier classification — shared across all engines.

The user's hybrid tier policy (locked in via the post-audit thread):

* **NRFI / YRFI** — raw probability ladder. Markets are symmetric
  (~50/50, both sides at -110), so probability and edge are
  interchangeable enough that the operator-friendly "70%+ side is
  Elite conviction" framing wins.
* **Props / full-game** — edge ladder (model_p − vig-adjusted market_p).
  Non-symmetric markets (favorites at -150, props at -120) require
  edge thresholds to be meaningful — a 60% prediction on a -150
  favorite is a fade, not a play.

Tiers map onto the deterministic-core's existing letter-grade buckets
(`ConfidenceScorer.grade`) so we keep one grading system, not two
parallel ones. The mapping is documented in `tier_to_grade()`.

Branding (2026-05-01 unification)
---------------------------------

Per the user's "Facts. Not Feelings." brand direction, the highest
tier is named **ELITE**, not LOCK — the engine sells data and
projection quality, not guarantees. The conviction ladder is
**tier-only** (no side-aware overrides) so a cold visitor reads the
key once and trusts it across every market:

* **Electric Blue** — Elite conviction (≥70% side prob OR ≥8pp edge).
* **Deep Green** — Strong conviction (64-69%), regardless of NRFI
  or YRFI side.
* **Amber / Yellow** — Moderate conviction (58-63%).
* **Slate** — Lean (55-57%); informational, not a recommended play.
* **Red** — No-play / pass (<55%). Red carries the universal
  "stop / no" semantic; reserving it for NO_PLAY keeps the meaning
  unambiguous.

History: an earlier branding pass (PR #96) used Red to mark
"Strong YRFI" as a positive-framing fade signal, but cold-traffic
testing showed it muddies the universal "red = no" read. Reverted
2026-05-01 — Red is now exclusively NO_PLAY.

Phase 6 (parlay builder) tracks units only, never W/L — it imports
the tier classifier here to gate which legs qualify, but stores its
own ledger separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Tier enum + thresholds
# ---------------------------------------------------------------------------


class Tier(str, Enum):
    """Conviction tier for a single pick."""

    ELITE = "ELITE"
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    LEAN = "LEAN"
    NO_PLAY = "NO_PLAY"

    @property
    def is_qualifying(self) -> bool:
        """True for tiers we record in the ledger (LEAN and above)."""
        return self in (Tier.ELITE, Tier.STRONG, Tier.MODERATE, Tier.LEAN)

    @property
    def is_betting_tier(self) -> bool:
        """True for tiers the operator should actually stake on
        (LEAN is content-only per the user's audit policy)."""
        return self in (Tier.ELITE, Tier.STRONG, Tier.MODERATE)


# NRFI / YRFI raw-probability ladder — applied to whichever side is
# being staked (NRFI prob for an NRFI pick, YRFI prob for a YRFI pick).
NRFI_PROB_THRESHOLDS: tuple[tuple[float, Tier], ...] = (
    (0.70, Tier.ELITE),
    (0.64, Tier.STRONG),
    (0.58, Tier.MODERATE),
    (0.55, Tier.LEAN),
)

# Edge-based ladder (model_p − vig-adjusted market_p, in pp / 0..1).
EDGE_THRESHOLDS: tuple[tuple[float, Tier], ...] = (
    (0.08, Tier.ELITE),
    (0.05, Tier.STRONG),
    (0.03, Tier.MODERATE),
    (0.01, Tier.LEAN),
)

# Markets that use the NRFI raw-probability ladder.
SYMMETRIC_FIRST_INNING_MARKETS: frozenset[str] = frozenset({"NRFI", "YRFI"})


@dataclass(frozen=True)
class TierClassification:
    """Output of `classify_tier`. Carries the tier plus the threshold
    band that triggered it for downstream display."""
    tier: Tier
    basis: str           # "raw_probability" | "edge"
    value: float         # the prob or edge that drove the decision
    band_lower: float    # threshold the value cleared
    band_upper: float    # next-tier-up threshold (1.0 / inf for top tier)


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------


def classify_tier(
    *,
    market_type: str,
    side_probability: Optional[float] = None,
    edge: Optional[float] = None,
) -> TierClassification:
    """Decide the conviction tier for a single pick.

    Parameters
    ----------
    market_type : Market identifier ("NRFI" / "YRFI" / "ML" / "Total" / ...).
        NRFI/YRFI route to the symmetric raw-probability ladder; everything
        else routes to the edge ladder.
    side_probability : Calibrated model probability of the side being
        staked, in [0, 1]. Required for symmetric markets.
    edge : `model_p − vig_adjusted_market_p` for the side being staked,
        in [-1, 1]. Required for non-symmetric markets.

    Returns
    -------
    TierClassification with the tier, basis label, value that drove it,
    and the threshold band so the email/dashboard can render the
    "65.4% (STRONG band 64-70%)" caption.
    """
    if market_type in SYMMETRIC_FIRST_INNING_MARKETS:
        if side_probability is None:
            raise ValueError(f"{market_type} requires side_probability")
        return _classify_by_ladder(
            value=float(side_probability),
            ladder=NRFI_PROB_THRESHOLDS,
            basis="raw_probability",
        )

    if edge is None:
        raise ValueError(
            f"{market_type} requires `edge` (edge-based ladder)"
        )
    return _classify_by_ladder(
        value=float(edge),
        ladder=EDGE_THRESHOLDS,
        basis="edge",
    )


def _classify_by_ladder(
    *, value: float, ladder: tuple[tuple[float, Tier], ...], basis: str,
) -> TierClassification:
    """Walk the ladder from highest threshold down; first match wins."""
    last_threshold = float("inf")
    for threshold, tier in ladder:
        if value >= threshold:
            return TierClassification(
                tier=tier, basis=basis, value=value,
                band_lower=threshold, band_upper=last_threshold,
            )
        last_threshold = threshold
    return TierClassification(
        tier=Tier.NO_PLAY, basis=basis, value=value,
        band_lower=float("-inf"),
        band_upper=ladder[-1][0] if ladder else 0.0,
    )


# ---------------------------------------------------------------------------
# Tier → operator policy
# ---------------------------------------------------------------------------


# Per the audit-locked stake policy. These are the recommended Kelly
# multipliers per tier; the actual stake is `kelly_full * multiplier`.
TIER_KELLY_MULTIPLIER: dict[Tier, float] = {
    Tier.ELITE:    0.75,    # midpoint of 0.5–1.0×
    Tier.STRONG:   0.375,   # midpoint of 0.25–0.5×
    Tier.MODERATE: 0.175,   # midpoint of 0.10–0.25×
    Tier.LEAN:     0.0,     # content-only, no stake
    Tier.NO_PLAY:  0.0,
}


# Default hex per tier — used when the renderer doesn't supply a side
# (e.g., props / full-game where the side concept is just Over/Under).
# Tier-only color ladder (2026-05-01). Hex codes match
# `website/tailwind.config.js` conviction-* tokens so the engine
# output and the website render the same colors. The previous
# side-aware override that flipped STRONG-YRFI to Red was reverted —
# Red now means NO_PLAY ("stop / pass") universally.
TIER_COLOR_HEX: dict[Tier, str] = {
    Tier.ELITE:    "#22d3ff",   # Electric Blue
    Tier.STRONG:   "#10b981",   # Deep Green
    Tier.MODERATE: "#f59e0b",   # Amber
    Tier.LEAN:     "#64748b",   # Slate
    Tier.NO_PLAY:  "#ef4444",   # Red — semantic stop
}


# Operator-facing color band labels — what the email row shows after
# the conviction percentage ("78.4% Conviction · Electric Blue").
TIER_COLOR_BAND_LABEL: dict[Tier, str] = {
    Tier.ELITE:    "Electric Blue",
    Tier.STRONG:   "Deep Green",
    Tier.MODERATE: "Amber",
    Tier.LEAN:     "Slate",
    Tier.NO_PLAY:  "Red",
}


def color_hex_for_pick(tier: Tier, market_type: str) -> str:
    """Tier color. ``market_type`` is preserved as a parameter for
    backwards compatibility with existing callers but is ignored —
    the conviction ladder is tier-only as of 2026-05-01.
    """
    return TIER_COLOR_HEX[tier]


def color_band_label_for_pick(tier: Tier, market_type: str) -> str:
    """Tier band label. ``market_type`` is preserved for
    backwards-compatible call sites; ignored by the lookup."""
    return TIER_COLOR_BAND_LABEL[tier]


# Letter-grade mapping aligned with the existing
# `edge_equation.math.scoring.ConfidenceScorer` ladder. Single grading
# system, not two parallel ones.
TIER_TO_GRADE: dict[Tier, str] = {
    Tier.ELITE:    "A+",
    Tier.STRONG:   "A",
    Tier.MODERATE: "B",
    Tier.LEAN:     "C",
    Tier.NO_PLAY:  "F",
}


def tier_to_grade(tier: Tier) -> str:
    """Map a tier to the deterministic core's letter grade."""
    return TIER_TO_GRADE[tier]


def kelly_multiplier(tier: Tier) -> float:
    """Recommended Kelly fraction for a tier. 0 for LEAN / NO_PLAY."""
    return TIER_KELLY_MULTIPLIER[tier]


def color_hex(tier: Tier) -> str:
    """Visual brand color for a tier. Equivalent to
    ``color_hex_for_pick(tier, market_type)`` — both ignore market
    side (the conviction ladder is tier-only as of 2026-05-01).
    """
    return TIER_COLOR_HEX[tier]


# ---------------------------------------------------------------------------
# Conviction key — the legend rendered at the top of every daily email
# ---------------------------------------------------------------------------


def render_conviction_key() -> str:
    """Plain-text legend explaining the color/conviction system.

    Sits at the top of the daily email so the operator (and any forwarded
    reader) can decode the color tokens in each pick row at a glance.
    """
    lines = [
        "CONVICTION KEY",
        "═" * 60,
        "  Electric Blue   ≥70% / ≥8pp edge   Elite conviction",
        "  Deep Green      64-69%             Strong conviction (either side)",
        "  Amber           58-63%             Moderate conviction",
        "  Slate           55-57%             Lean (content-only)",
        "  Red             <55%               No play / pass",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Premium disclaimer footer
# ---------------------------------------------------------------------------


PREMIUM_DISCLAIMER = (
    "These are data projections only — not betting advice. Premium gives "
    "deeper model insights (full SHAP drivers, exact Kelly, parlay reasoning). "
    "Facts. Not Feelings."
)


def render_premium_disclaimer() -> str:
    """Standard disclaimer footer for premium emails."""
    return PREMIUM_DISCLAIMER
