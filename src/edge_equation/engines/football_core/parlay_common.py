"""Shared parlay-engine plumbing for NFL + NCAAF.

NFL and NCAAF share the football-market vocabulary
(`football_core.markets`) and a near-identical per-row ``*Output``
shape. To keep duplication low, the strict-policy parlay engine is
implemented once here and parameterised by the per-sport rules
object + namespace name. Each sport's `engines/<sport>/` module
provides a thin façade that wires its rules class into these helpers.

Strict policy (audit-locked, identical to MLB / WNBA):
* 3–6 legs only.
* Each leg ≥4pp edge against the de-vigged closing line OR ELITE
  tier (Signal Elite / LOCK).
* Combined EV positive after vig.
* No forced parlays — emits "No qualified parlay today …" when no
  combination passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any, Iterable, Optional, Protocol, Sequence

from edge_equation.engines.mlb.game_results_parlay import EnrichedLeg
from edge_equation.engines.parlay import (
    ParlayCandidate,
    ParlayConfig,
    ParlayLeg,
    build_parlay_candidates,
    render_candidate,
)
from edge_equation.engines.parlay.builder import simulate_correlated_joint_prob
from edge_equation.engines.tiering import Tier, classify_tier
from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Protocol — duck-typed shape every football rules class implements.
# ---------------------------------------------------------------------------


class FootballParlayRules(Protocol):
    """Protocol every per-sport rules class satisfies (NFL / NCAAF)."""

    min_legs: int
    max_legs: int
    min_leg_edge_frac: float
    elite_bypass_tier: Tier
    min_leg_confidence: float
    min_joint_prob: float
    min_ev_units: float
    default_stake_units: float
    min_leg_clv_pp: float
    max_abs_correlation: float
    mc_trials: int
    mc_seed: int

    def leg_qualifies(
        self, *,
        market_type: str,
        edge_frac: float,
        tier: Tier,
        confidence: float,
        clv_pp: float,
        market_universe: str,
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Card payload (used by both universes — game_results + player_props).
# ---------------------------------------------------------------------------


@dataclass
class FootballParlayCard:
    """Generic card payload for football parlay engines.

    `note` and `transparency_note` flow from the per-sport rules
    module so a future copy edit lands in one place.
    """

    target_date: str
    sport: str                      # 'nfl' | 'ncaaf'
    universe: str                   # 'game_results' | 'player_props'
    candidates: list[ParlayCandidate] = field(default_factory=list)
    n_legs_pool: int = 0
    n_legs_after_gate: int = 0
    explanation: str = ""
    top_board_text: str = ""
    note: str = ""
    transparency_note: str = ""

    @property
    def has_qualified(self) -> bool:
        return bool(self.candidates)


# ---------------------------------------------------------------------------
# Leg adapter — football `*Output` → `EnrichedLeg`.
# ---------------------------------------------------------------------------


def _football_output_to_enriched(
    out: Any, *,
    rules: FootballParlayRules,
    market_universe: str,
) -> Optional[EnrichedLeg]:
    """Adapt a football `*Output` (NFL or NCAAF) to an `EnrichedLeg`.

    Reads only named attributes so any duck-typed object with the
    same shape works (used by tests).
    """
    market = str(getattr(out, "market_type", "") or "")
    if market_universe == "game_results":
        if market not in rules.allowed_game_result_markets:
            return None
    elif market_universe == "player_props":
        if market not in rules.allowed_player_prop_markets:
            return None
    else:
        return None

    side = str(getattr(out, "side", "") or "")
    home = str(getattr(out, "home_tricode", "") or "")
    away = str(getattr(out, "away_tricode", "") or "")
    line_value = getattr(out, "line_value", None)

    side_str = _side_for_market(market, side, home, away, line_value)
    label = _label_for_market(
        market, side, home, away, line_value,
        player=str(getattr(out, "player_name", "") or ""),
        market_label=str(getattr(out, "market_label", market) or market),
    )

    try:
        tier_obj = Tier(
            str(getattr(out, "tier", "") or "NO_PLAY").upper()
        )
    except ValueError:
        tier_obj = Tier.NO_PLAY

    edge_pp = float(getattr(out, "edge_pp", 0.0) or 0.0)
    edge_frac = edge_pp / 100.0
    confidence = float(getattr(out, "confidence", 0.30) or 0.30)
    clv_pp = float(getattr(out, "clv_pp", 0.0) or 0.0)

    # When the per-row output didn't pre-classify a tier (or marked
    # NO_PLAY), fall back to the shared classify_tier ladder so the
    # ELITE bypass still works for high-edge picks the per-engine
    # gate already qualified.
    if tier_obj == Tier.NO_PLAY:
        try:
            clf = classify_tier(
                market_type=market,
                edge=edge_frac,
                side_probability=float(
                    getattr(out, "model_prob", 0.0) or 0.0,
                ),
            )
            tier_obj = clf.tier
        except Exception:
            tier_obj = Tier.NO_PLAY

    leg = ParlayLeg(
        market_type=market,
        side=side_str,
        side_probability=float(getattr(out, "model_prob", 0.0) or 0.0),
        american_odds=float(
            getattr(out, "american_odds", -110.0) or -110.0,
        ),
        tier=tier_obj,
        game_id=str(getattr(out, "event_id", "") or f"{away}@{home}"),
        # Same-player correlation routing keys off ``player_id``;
        # football outputs surface ``player_name`` for prop rows and
        # nothing for game rows.
        player_id=(
            str(getattr(out, "player_name", "") or "")
            if market_universe == "player_props" else None
        ),
        label=label,
    )
    return EnrichedLeg(
        leg=leg,
        edge_frac=float(edge_frac),
        confidence=confidence,
        clv_pp=clv_pp,
    )


def _side_for_market(
    market: str, side: str, home: str, away: str, line_value,
) -> str:
    side_l = (side or "").lower()
    team = home if "home" in side_l or side == home else away if (
        "away" in side_l or side == away
    ) else side
    if market in ("ML", "First_Half_ML", "First_Quarter_ML"):
        return f"{team or side} ML"
    if market in (
        "Spread", "Alternate_Spread",
        "First_Half_Spread", "First_Quarter_Spread",
    ):
        if line_value is None:
            return f"{team or side} Spread"
        return f"{team or side} {line_value:+g}"
    if market in (
        "Total", "Alternate_Total", "Team_Total",
        "First_Half_Total", "First_Quarter_Total",
    ):
        if line_value is None:
            return side or "Over"
        direction = "Over" if "over" in side_l else "Under"
        return f"{direction} {line_value:g}"
    # Player props: side is "Over"/"Under"; line_value is the prop
    # line.
    if line_value is None:
        return side or "Over"
    direction = "Over" if "over" in side_l else "Under"
    return f"{direction} {line_value:g}"


def _label_for_market(
    market: str, side: str, home: str, away: str, line_value,
    *, player: str = "", market_label: str = "",
) -> str:
    if player:
        return f"{player} {market_label or market} {side} {line_value:g}".strip()
    if market in ("ML", "First_Half_ML", "First_Quarter_ML"):
        suffix = (
            " (1H)" if market == "First_Half_ML"
            else " (1Q)" if market == "First_Quarter_ML"
            else ""
        )
        return f"{home or side}{suffix} ML"
    if market in (
        "Spread", "Alternate_Spread",
        "First_Half_Spread", "First_Quarter_Spread",
    ):
        suffix = (
            " (1H)" if market == "First_Half_Spread"
            else " (1Q)" if market == "First_Quarter_Spread"
            else ""
        )
        line_str = "" if line_value is None else f" {line_value:+g}"
        return f"{home or side}{suffix} Spread{line_str}"
    if market == "Team_Total":
        line_str = "" if line_value is None else f" {line_value:g}"
        return f"{home} Team Total {side}{line_str}"
    if market in (
        "Total", "Alternate_Total",
        "First_Half_Total", "First_Quarter_Total",
    ):
        suffix = (
            " (1H)" if market == "First_Half_Total"
            else " (1Q)" if market == "First_Quarter_Total"
            else ""
        )
        line_str = "" if line_value is None else f" {line_value:g}"
        direction = side.title() if side else "Over"
        return f"{suffix}Total {direction}{line_str}".strip()
    return f"{market} {side}".strip()


# ---------------------------------------------------------------------------
# Public leg builder + filter.
# ---------------------------------------------------------------------------


def build_legs(
    *,
    outputs: Sequence,
    rules: FootballParlayRules,
    market_universe: str,
) -> list[EnrichedLeg]:
    """Convert per-row outputs into the leg pool for a given universe."""
    legs: list[EnrichedLeg] = []
    for o in outputs:
        leg = _football_output_to_enriched(
            o, rules=rules, market_universe=market_universe,
        )
        if leg is not None:
            legs.append(leg)
    return legs


def filter_legs_by_strict_rules(
    legs: Iterable[EnrichedLeg], *,
    rules: FootballParlayRules,
    market_universe: str,
) -> list[EnrichedLeg]:
    """Drop legs that don't pass the strict per-leg gate."""
    out: list[EnrichedLeg] = []
    for enriched in legs:
        if rules.leg_qualifies(
            market_type=enriched.market_type,
            edge_frac=enriched.edge_frac,
            tier=enriched.tier,
            confidence=enriched.confidence,
            clv_pp=enriched.clv_pp,
            market_universe=market_universe,
        ):
            out.append(enriched)
    return out


# ---------------------------------------------------------------------------
# Card builder.
# ---------------------------------------------------------------------------


def build_parlay_card(
    *,
    sport: str,
    universe: str,
    outputs: Sequence,
    target_date: Optional[str],
    rules: FootballParlayRules,
    note: str,
    transparency_note: str,
    no_qualified_message: str,
    top_n: int = 3,
) -> FootballParlayCard:
    target = target_date or _date.today().isoformat()
    pool = build_legs(
        outputs=outputs, rules=rules, market_universe=universe,
    )
    n_pool = len(pool)
    qualifying = filter_legs_by_strict_rules(
        pool, rules=rules, market_universe=universe,
    )
    n_after = len(qualifying)
    log.info(
        "%s %s parlay: %d/%d legs cleared the strict gate",
        sport.upper(), universe, n_after, n_pool,
    )
    # CLV snapshot: per-leg CLV is captured upstream by each per-sport
    # daily run via the shared `exporters.mlb.clv_tracker.ClvTracker`
    # plumbing. The combined-ticket snapshot is logged below once the
    # candidate list is built.

    candidates: list[ParlayCandidate] = []
    if n_after >= rules.min_legs:
        pcfg = ParlayConfig(
            min_tier=Tier.LEAN,
            max_legs=rules.max_legs,
            default_stake_units=rules.default_stake_units,
            min_joint_prob=rules.min_joint_prob,
            min_ev_units=rules.min_ev_units,
            mc_trials=rules.mc_trials,
            mc_seed=rules.mc_seed,
            max_abs_correlation=rules.max_abs_correlation,
        )
        plain_legs = [e.leg for e in qualifying]
        all_candidates = build_parlay_candidates(plain_legs, config=pcfg)
        candidates = [
            c for c in all_candidates if c.n_legs >= rules.min_legs
        ]

    explanation = "" if candidates else no_qualified_message
    top = candidates[:top_n]
    header = f"{sport.upper()} {universe.replace('_', '-').upper()} PARLAY"
    top_board = render_card_block(
        top, header=header, note=note, transparency_note=transparency_note,
        no_qualified_message=no_qualified_message,
    )

    # Combined-ticket CLV snapshot via the shared MLB helper.
    from edge_equation.engines.mlb.game_results_parlay import (
        log_parlay_clv_snapshot,
    )
    log_parlay_clv_snapshot(
        candidates=top,
        universe=f"{sport}_{universe}",
        target_date=target,
    )

    return FootballParlayCard(
        target_date=target,
        sport=sport,
        universe=universe,
        candidates=top,
        n_legs_pool=n_pool,
        n_legs_after_gate=n_after,
        explanation=explanation,
        top_board_text=top_board,
        note=note,
        transparency_note=transparency_note,
    )


# ---------------------------------------------------------------------------
# Renderer.
# ---------------------------------------------------------------------------


def render_card_block(
    candidates: Sequence[ParlayCandidate], *,
    header: str,
    note: str,
    transparency_note: str,
    no_qualified_message: str,
) -> str:
    if not candidates:
        return (
            f"{header}\n"
            f"{'═' * 60}\n"
            f"  {transparency_note}\n"
            f"  {no_qualified_message}\n"
        )
    top = list(candidates)
    out_lines = [
        f"{header} — Top {len(top)} qualified ticket(s)",
        f"  Note: {note}",
        f"  {transparency_note}",
        "═" * 60,
    ]
    for i, cand in enumerate(top, 1):
        rendered = render_candidate(cand)
        prefix = f"{i:>2}.  "
        rendered = rendered.replace("\n", "\n" + " " * len(prefix))
        out_lines.append(prefix + rendered)
        out_lines.append("")
    return "\n".join(out_lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Joint-prob helper — shared across both engines for the backtest.
# ---------------------------------------------------------------------------


def joint_probability(
    legs: Sequence[ParlayLeg], *,
    rules: FootballParlayRules,
) -> float:
    return simulate_correlated_joint_prob(
        legs,
        n_trials=rules.mc_trials,
        seed=rules.mc_seed,
        max_abs_correlation=rules.max_abs_correlation,
    )
