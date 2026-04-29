"""Pairwise correlation lookup for parlay legs.

The MC joint-probability simulation in `builder.simulate_correlated_joint_prob`
uses a Gaussian copula keyed off this table. Three correlation regimes
matter for our parlay shape:

* **Different games, different players** — assumed independent (ρ = 0).
  Vast majority of cross-market parlays land here.
* **Same game** — first-inning runs, full-game total, ML, and run line
  share the same generative process so they're moderately correlated.
  E.g., NRFI ↔ Total Under is positive (low first inning predicts low
  total), NRFI ↔ HOME ML is roughly neutral (NRFI is a pitching-duel
  signal that doesn't favor either side strongly).
* **Same player, different markets** — HRs / total bases / hits are
  the same offensive event in different aggregation windows, hence
  highly positively correlated. K's are largely orthogonal to bat
  outcomes since they're a pitching event applied to one batter.

The values below are **rough magnitudes** drawn from public studies
(Pinnacle's parlay-correlation guide, BetLabs' MLB correlation series,
and Pradier / Olbrich 2018). They're conservative — when in doubt the
table errs on the higher-correlation side so the joint-prob estimate
is honest about parlay risk.

The `correlation_for_pair()` API is total: any pair not in the table
returns 0.0 (the safe-but-strict assumption). Two legs that touch the
same market in the same game (e.g., NRFI + YRFI) MUST NOT be combined
— they're not modelled here at all. The builder filters those out
before reaching the correlation step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ParlayLegContext:
    """Minimal context needed to look up correlation between two legs.

    `game_id` and `player_id` are optional — cross-game cross-player
    legs map to the most common case (independence). `market_type` is
    the canonical name from `tiering.SYMMETRIC_FIRST_INNING_MARKETS`
    plus the engine's full-game / props market vocabulary.
    `side` distinguishes Over/Under, Home/Away, etc. when the same
    market produces opposite outcomes.
    """
    market_type: str
    side: str
    game_id: Optional[str] = None
    player_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Same-game correlation table (markets keyed canonical → canonical, |ρ|)
# ---------------------------------------------------------------------------

# Symmetric pairs — order doesn't matter for the lookup. Values reflect
# the *signed* correlation between the most natural side pairing
# (e.g., "Under" × "Under" for first-inning + full-game totals). The
# `correlation_for_pair()` resolver flips the sign when the legs pick
# opposite sides of the obvious pairing.
_SAME_GAME_CORR: dict[tuple[str, str], float] = {
    # NRFI = Under 0.5 first-inning runs.
    # YRFI is just NRFI's flipped side; never combined with NRFI in a parlay.
    ("NRFI", "Total"):        0.30,   # low 1st inning → low full-game total
    ("NRFI", "F5_Total"):     0.50,   # tighter window — stronger positive
    ("NRFI", "ML"):           0.05,   # marginal — pitching-duel signal
    ("NRFI", "Run_Line"):     0.05,   # ~ML
    ("YRFI", "Total"):        0.30,
    ("YRFI", "F5_Total"):     0.50,
    ("YRFI", "ML"):           0.05,
    # F5 = first-five-innings totals / ML.
    ("F5_Total", "Total"):    0.55,   # F5 totals strongly drive full-game totals
    ("F5_ML", "ML"):          0.65,   # F5 winner usually wins game
    ("F5_Total", "ML"):       0.10,
    # Same-game ML × Run Line on the same side — almost always co-hit
    # (RL covers when ML wins by ≥2). Combining them is mostly
    # double-counting; keep them out of parlays via builder gate.
    ("ML", "Run_Line"):       0.70,
    ("ML", "Total"):           0.05,
    ("Run_Line", "Total"):     0.05,
}


# ---------------------------------------------------------------------------
# Same-player correlation table (player props within one game)
# ---------------------------------------------------------------------------

_SAME_PLAYER_CORR: dict[tuple[str, str], float] = {
    # Hitting markets — share the same plate-appearance generator.
    ("HR", "Total_Bases"):    0.60,
    ("HR", "Hits"):           0.45,
    ("Hits", "Total_Bases"):  0.75,
    ("Hits", "Singles"):      0.55,
    ("Hits", "RBI"):          0.30,
    ("Total_Bases", "RBI"):   0.40,
    ("HR", "RBI"):            0.45,
    # Pitching markets — strikeouts are largely orthogonal to most
    # hitting outcomes for the OPPOSING team but for the same player
    # (e.g., a hitter's K-prop) it's a hard negative.
    ("Hits", "K"):           -0.40,
    ("Total_Bases", "K"):    -0.35,
    ("HR", "K"):             -0.30,
    # Pitcher's own props.
    ("K", "Outs"):            0.50,
    ("K", "Earned_Runs"):    -0.30,
    ("Outs", "Earned_Runs"): -0.40,
}


# Same-market opposite-side pairs that the BUILDER must filter out
# entirely (we never want them in a parlay). E.g., NRFI + YRFI on the
# same game = book a definite loss + win cancellation.
_SAME_MARKET_OPPOSITE_SIDES: dict[str, set[str]] = {
    "NRFI": {"YRFI"},
    "YRFI": {"NRFI"},
}


# ---------------------------------------------------------------------------
# Public lookup
# ---------------------------------------------------------------------------


def correlation_for_pair(
    a: ParlayLegContext, b: ParlayLegContext,
) -> float:
    """Return signed correlation between legs `a` and `b`.

    Defaults to 0.0 (independent) for pairs not in the lookup tables.
    Negative when the legs sit on opposite sides of the obvious
    pairing (e.g., NRFI + Total Over → negative because low first
    inning is *negatively* correlated with high full-game total).
    """
    if are_mutually_exclusive(a, b):
        # Defensive — builder shouldn't pair these but if it does the
        # MC needs to see a hard ρ that flags the impossibility.
        return -1.0

    same_game = bool(
        a.game_id is not None and b.game_id is not None
        and a.game_id == b.game_id
    )
    same_player = bool(
        a.player_id is not None and b.player_id is not None
        and a.player_id == b.player_id
    )

    if same_player:
        rho = _table_lookup(
            _SAME_PLAYER_CORR, a.market_type, b.market_type,
        )
        if rho is not None:
            return _apply_side_sign(rho, a.side, b.side, a.market_type)
        # Same player but no entry — fall through to same-game lookup.

    if same_game:
        rho = _table_lookup(
            _SAME_GAME_CORR, a.market_type, b.market_type,
        )
        if rho is not None:
            return _apply_side_sign(rho, a.side, b.side, a.market_type)

    # Different games (or no entry in either table) — assume independence.
    return 0.0


def are_mutually_exclusive(
    a: ParlayLegContext, b: ParlayLegContext,
) -> bool:
    """True when `a` and `b` describe outcomes that cannot co-occur.

    Currently: NRFI + YRFI (or any same-market opposite-side pair) on
    the same game. Extend as new market_types land that have natural
    opposite-side definitions.
    """
    if a.game_id is None or b.game_id is None or a.game_id != b.game_id:
        return False
    excludes = _SAME_MARKET_OPPOSITE_SIDES.get(a.market_type, set())
    return b.market_type in excludes


def _table_lookup(
    table: dict[tuple[str, str], float], m1: str, m2: str,
) -> Optional[float]:
    if (m1, m2) in table:
        return table[(m1, m2)]
    if (m2, m1) in table:
        return table[(m2, m1)]
    return None


def _apply_side_sign(
    rho: float, side_a: str, side_b: str, market_type: str,
) -> float:
    """Flip the table value when the two legs pick opposite sides
    of the natural same-direction pairing.

    The lookup tables are keyed assuming "natural-direction" sides
    (Under × Under for totals correlations, Over × Over for hit-prop
    correlations). When one leg flips direction, the correlation
    flips sign.
    """
    natural_a = _natural_side(side_a, market_type)
    natural_b = _natural_side(side_b, market_type)
    if natural_a is None or natural_b is None:
        return rho
    if natural_a == natural_b:
        return rho
    return -rho


def _natural_side(side: str, market_type: str) -> Optional[str]:
    """Map a side label ('Over 0.5', 'Yankees ML', 'Under 8.5') to a
    coarse direction tag for sign flipping. Returns None when the side
    isn't side-direction-bearing (e.g., a moneyline)."""
    s = (side or "").strip().lower()
    if not s:
        return None
    if s.startswith("under"):
        return "low"
    if s.startswith("over"):
        return "high"
    # Moneylines don't have a direction in the totals sense — return
    # None so the sign isn't flipped.
    return None
