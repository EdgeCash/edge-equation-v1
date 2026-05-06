"""MLB player-props parlay engine — strict 3–6 leg builder.

The mirror of ``game_results_parlay`` for player-prop legs. It pulls
today's qualifying prop picks out of the existing
``engines.props_prizepicks`` runner, filters them through the strict
``MLBParlayRules`` gate, and asks the shared ``engines.parlay`` builder
to assemble candidate combos with correlation-adjusted Monte-Carlo
joint probability + EV gating.

Strict-policy rules (verbatim, identical to game-results parlay):
* 3–6 legs maximum.
* Each leg must clear ≥4pp edge against the de-vigged closing line OR
  be tier == ELITE (Signal Elite / LOCK).
* Combined EV after vig must remain positive.
* No forced parlays — emits "No qualified parlay today …" when nothing
  passes.
* No 8+ leg specials, no "fun" / high-odds-only tickets.

The two engines stay decoupled even though they share threshold
constants — game-results legs and player-props legs live in different
correlation regimes (same-game vs same-player), and same-game
multi-prop tickets need fresh per-player gating that the game-results
gate doesn't cover.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Iterable, Optional, Sequence

from edge_equation.engines.parlay import (
    ParlayCandidate,
    ParlayConfig,
    ParlayLeg,
    build_parlay_candidates,
    render_candidate,
)
from edge_equation.engines.parlay.builder import simulate_correlated_joint_prob
from edge_equation.engines.tiering import Tier
from edge_equation.utils.logging import get_logger

from .game_results_parlay import EnrichedLeg
from .thresholds import (
    MLB_PARLAY_RULES,
    MLBParlayRules,
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Daily card payload
# ---------------------------------------------------------------------------


@dataclass
class PlayerPropsParlayCard:
    """The card returned by the engine — packaged for the daily runner.

    Mirrors the `*Card` shape used by every other MLB engine
    (``PropsCard``, ``FullGameCard``, NRFI's email card) so the unified
    runner can plug it in without a special case.
    """

    target_date: str
    candidates: list[ParlayCandidate] = field(default_factory=list)
    n_legs_pool: int = 0
    n_legs_after_gate: int = 0
    explanation: str = ""
    top_board_text: str = ""
    note: str = PARLAY_CARD_NOTE
    # The audit-locked transparency sentence rendered alongside every
    # parlay block on the website + in the daily card.
    transparency_note: str = PARLAY_TRANSPARENCY_NOTE

    @property
    def has_qualified(self) -> bool:
        return bool(self.candidates)


# ---------------------------------------------------------------------------
# Leg adapter
# ---------------------------------------------------------------------------


def _prop_to_enriched(
    out, *, rules: MLBParlayRules,
) -> Optional[EnrichedLeg]:
    """Adapt a `props_prizepicks.output.PropOutput` to an `EnrichedLeg`.

    Returns None when the row's market isn't in the allowed
    player-props universe. Confidence + edge metadata are pulled from
    the prop output's audit fields.
    """
    market = str(getattr(out, "market_type", "") or "")
    if market not in rules.allowed_player_prop_markets:
        return None

    side = str(getattr(out, "side", "") or "Over")
    line_value = float(getattr(out, "line_value", 0.0) or 0.0)
    player_name = str(getattr(out, "player_name", "") or "")
    market_label = str(getattr(out, "market_label", market) or market)

    try:
        tier = Tier(str(getattr(out, "tier", "") or "NO_PLAY").upper())
    except ValueError:
        tier = Tier.NO_PLAY

    label = f"{player_name} {market_label} {side} {line_value:g}"
    side_str = f"{side} {line_value:g}"

    leg = ParlayLeg(
        market_type=market,
        side=side_str,
        side_probability=float(getattr(out, "model_prob", 0.0) or 0.0),
        american_odds=float(getattr(out, "american_odds", -110.0) or -110.0),
        tier=tier,
        game_id=str(getattr(out, "game_id", "") or ""),
        # ``player_id`` keys the same-player correlation table; we use
        # the player_name when no canonical id flows through. The
        # correlation lookup only checks for equality, so any stable
        # identifier is fine.
        player_id=str(getattr(out, "player_id", player_name) or player_name),
        label=label,
    )
    edge_pp = float(getattr(out, "edge_pp", 0.0) or 0.0)
    return EnrichedLeg(
        leg=leg,
        edge_frac=float(edge_pp / 100.0),
        confidence=float(getattr(out, "confidence", 0.30) or 0.30),
        clv_pp=float(getattr(out, "clv_pp", 0.0) or 0.0),
    )


# ---------------------------------------------------------------------------
# Public leg builder
# ---------------------------------------------------------------------------


def build_player_props_legs(
    *,
    prop_outputs: Sequence = (),
    rules: MLBParlayRules = MLB_PARLAY_RULES,
) -> list[EnrichedLeg]:
    """Convert today's prop outputs into the leg pool.

    ``prop_outputs`` is an iterable of ``PropOutput`` (or a duck-typed
    equivalent — only the named attrs are read).
    """
    legs: list[EnrichedLeg] = []
    for out in prop_outputs:
        leg = _prop_to_enriched(out, rules=rules)
        if leg is not None:
            legs.append(leg)
    return legs


# ---------------------------------------------------------------------------
# Strict-policy filter
# ---------------------------------------------------------------------------


def filter_legs_by_strict_rules(
    legs: Iterable[EnrichedLeg], *,
    rules: MLBParlayRules = MLB_PARLAY_RULES,
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
            market_universe="player_props",
        ):
            out.append(enriched)
    return out


# ---------------------------------------------------------------------------
# Main builder — strict-policy parlay candidates
# ---------------------------------------------------------------------------


def build_player_props_parlay(
    *,
    prop_outputs: Sequence = (),
    target_date: Optional[str] = None,
    rules: MLBParlayRules = MLB_PARLAY_RULES,
    top_n: int = 3,
) -> PlayerPropsParlayCard:
    """Build the ``PlayerPropsParlayCard`` for ``target_date``.

    Pipeline mirrors the game-results parlay engine — see
    ``game_results_parlay.build_game_results_parlay`` for the
    annotated walkthrough. The only differences here are the leg
    adapter (props vs full-game/NRFI) and the universe ("player_props"
    vs "game_results"), since same-player correlation routing
    (``HR + Total_Bases`` etc.) is keyed off ``player_id`` rather
    than ``game_id``.
    """
    target = target_date or _date.today().isoformat()
    pool = build_player_props_legs(prop_outputs=prop_outputs, rules=rules)
    n_pool = len(pool)
    qualifying = filter_legs_by_strict_rules(pool, rules=rules)
    n_after = len(qualifying)
    log.info(
        "MLB player-props parlay: %d/%d legs cleared the strict gate",
        n_after, n_pool,
    )
    # CLV snapshot: leg-level CLV is captured upstream by
    # `exporters.mlb.clv_tracker.ClvTracker.record_picks` during the
    # props engine's own daily run; the combined-ticket snapshot is
    # written below via `log_parlay_clv_snapshot()` once candidates
    # are finalized.

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
    explanation = (
        ""
        if candidates else
        NO_QUALIFIED_PARLAY_MESSAGE
    )

    top = candidates[:top_n]
    top_board = render_card_block(top, header="PLAYER-PROPS PARLAY")
    # Combined-ticket CLV snapshot — leg CLV already logged by the
    # per-engine ClvTracker during the props daily run.
    from .game_results_parlay import log_parlay_clv_snapshot
    log_parlay_clv_snapshot(
        candidates=top, universe="player_props", target_date=target,
    )

    return PlayerPropsParlayCard(
        target_date=target,
        candidates=top,
        n_legs_pool=n_pool,
        n_legs_after_gate=n_after,
        explanation=explanation,
        top_board_text=top_board,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_card_block(
    candidates: Sequence[ParlayCandidate], *,
    header: str = "PLAYER-PROPS PARLAY",
    note: str = PARLAY_CARD_NOTE,
    transparency_note: str = PARLAY_TRANSPARENCY_NOTE,
) -> str:
    """Plain-text block ready to drop into the daily email."""
    if not candidates:
        return (
            f"{header}\n"
            f"{'═' * 60}\n"
            f"  {transparency_note}\n"
            f"  {NO_QUALIFIED_PARLAY_MESSAGE}\n"
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
# Public engine class — registry-friendly façade
# ---------------------------------------------------------------------------


@dataclass
class MLBPlayerPropsParlayEngine:
    """Class the central engine_registry hands to the daily runner."""

    rules: MLBParlayRules = MLB_PARLAY_RULES
    top_n: int = 3

    name: str = "mlb_player_props_parlay"

    def run(
        self, *,
        prop_outputs: Sequence = (),
        target_date: Optional[str] = None,
    ) -> PlayerPropsParlayCard:
        """Build today's card and return it. Never raises."""
        try:
            return build_player_props_parlay(
                prop_outputs=prop_outputs,
                target_date=target_date,
                rules=self.rules,
                top_n=self.top_n,
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                "MLBPlayerPropsParlayEngine: build failed (%s): %s",
                type(e).__name__, e,
            )
            return PlayerPropsParlayCard(
                target_date=target_date or _date.today().isoformat(),
                explanation=(
                    f"{NO_QUALIFIED_PARLAY_MESSAGE} (build error: "
                    f"{type(e).__name__})"
                ),
            )

    @staticmethod
    def joint_probability(legs: Sequence[ParlayLeg], *,
                          rules: MLBParlayRules = MLB_PARLAY_RULES) -> float:
        """Surface the correlation-adjusted joint probability MC."""
        return simulate_correlated_joint_prob(
            legs,
            n_trials=rules.mc_trials,
            seed=rules.mc_seed,
            max_abs_correlation=rules.max_abs_correlation,
        )
