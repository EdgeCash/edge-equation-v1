"""WNBA player-props parlay engine — strict 3–6 leg builder.

The mirror of `engines.wnba.game_results_parlay` for player-prop
legs. Pulls today's qualifying prop picks out of the existing WNBA
runner (`engines.wnba.run_daily.WNBARunner`), filters them through
the strict `WNBAParlayRules` gate, and asks the shared
`engines.parlay` builder to assemble candidate combos with
correlation-adjusted Monte-Carlo joint probability + EV gating.

Strict-policy rules are identical to the MLB / WNBA game-results
parlay engines — see `engines.wnba.thresholds` for the audit-locked
constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Iterable, Optional, Sequence

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

from .thresholds import (
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
    WNBA_PARLAY_RULES,
    WNBAParlayRules,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Card payload
# ---------------------------------------------------------------------------


@dataclass
class WNBAPlayerPropsParlayCard:
    target_date: str
    candidates: list[ParlayCandidate] = field(default_factory=list)
    n_legs_pool: int = 0
    n_legs_after_gate: int = 0
    explanation: str = ""
    top_board_text: str = ""
    note: str = PARLAY_CARD_NOTE
    transparency_note: str = PARLAY_TRANSPARENCY_NOTE

    @property
    def has_qualified(self) -> bool:
        return bool(self.candidates)


# ---------------------------------------------------------------------------
# Leg adapter
# ---------------------------------------------------------------------------


def _market_str(market) -> str:
    if hasattr(market, "value"):
        return str(market.value)
    return str(market or "")


def _wnba_prop_to_enriched(
    out, *, rules: WNBAParlayRules,
) -> Optional[EnrichedLeg]:
    """Adapt a WNBA prop `Output` (or duck-typed equivalent) to an
    `EnrichedLeg`."""
    market = _market_str(getattr(out, "market", "") or "")
    if market not in rules.allowed_player_prop_markets:
        return None

    player = str(getattr(out, "player", "") or "")
    team = str(getattr(out, "team", "") or "")
    line = float(getattr(out, "line", 0.0) or 0.0)
    side_dir = str(getattr(out, "side", "") or "Over").title()
    side = f"{side_dir} {line:g}"
    label = f"{player} {market.title()} {side_dir} {line:g}"

    prob = float(getattr(out, "probability", 0.0) or 0.0)
    edge_pp = float(getattr(out, "edge_pp", 0.0) or 0.0)
    if not edge_pp:
        edge_pp = (prob - 0.524) * 100.0
    edge_frac = edge_pp / 100.0

    confidence = float(getattr(out, "confidence", 0.30) or 0.30)
    clv_pp = float(getattr(out, "clv_pp", 0.0) or 0.0)

    try:
        clf = classify_tier(
            market_type=market.upper() if market in (
                "points", "rebounds", "assists", "pra", "3pm",
                "steals", "blocks", "stocks", "turnovers",
            ) else market,
            edge=edge_frac, side_probability=prob,
        )
        tier = clf.tier
    except Exception:
        tier = Tier.NO_PLAY

    american = float(getattr(out, "american_odds", 0.0) or 0.0)
    if not american:
        meta = getattr(out, "meta", None)
        decimal = (
            float(meta.get("decimal_odds", 0.0)) if isinstance(meta, dict)
            else 0.0
        )
        if decimal > 1.0:
            american = (
                (decimal - 1.0) * 100.0 if decimal >= 2.0
                else -100.0 / (decimal - 1.0)
            )
        else:
            american = -110.0

    leg = ParlayLeg(
        market_type=market,
        side=side,
        side_probability=prob,
        american_odds=american,
        tier=tier,
        game_id=str(getattr(out, "game_id", "") or team),
        # Same-player correlation lookup is keyed off ``player_id``.
        # The WNBA `Output` doesn't surface a stable id today, so we
        # use the player_name — the lookup only checks equality.
        player_id=str(getattr(out, "player_id", player) or player),
        label=label,
    )
    return EnrichedLeg(
        leg=leg,
        edge_frac=float(edge_frac),
        confidence=confidence,
        clv_pp=clv_pp,
    )


# ---------------------------------------------------------------------------
# Public leg builder + filter
# ---------------------------------------------------------------------------


def build_player_props_legs(
    *,
    wnba_prop_outputs: Sequence = (),
    rules: WNBAParlayRules = WNBA_PARLAY_RULES,
) -> list[EnrichedLeg]:
    """Convert today's WNBA prop outputs into the leg pool."""
    legs: list[EnrichedLeg] = []
    for out in wnba_prop_outputs:
        leg = _wnba_prop_to_enriched(out, rules=rules)
        if leg is not None:
            legs.append(leg)
    return legs


def filter_legs_by_strict_rules(
    legs: Iterable[EnrichedLeg], *,
    rules: WNBAParlayRules = WNBA_PARLAY_RULES,
) -> list[EnrichedLeg]:
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
# Card builder
# ---------------------------------------------------------------------------


def build_player_props_parlay(
    *,
    wnba_prop_outputs: Sequence = (),
    target_date: Optional[str] = None,
    rules: WNBAParlayRules = WNBA_PARLAY_RULES,
    top_n: int = 3,
) -> WNBAPlayerPropsParlayCard:
    target = target_date or _date.today().isoformat()
    pool = build_player_props_legs(
        wnba_prop_outputs=wnba_prop_outputs, rules=rules,
    )
    n_pool = len(pool)
    qualifying = filter_legs_by_strict_rules(pool, rules=rules)
    n_after = len(qualifying)
    log.info(
        "WNBA player-props parlay: %d/%d legs cleared the strict gate",
        n_after, n_pool,
    )
    # CLV snapshot: leg-level CLV captured upstream by the per-engine
    # closing-line snapshot job; combined-ticket CLV is logged below.

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
    explanation = "" if candidates else NO_QUALIFIED_PARLAY_MESSAGE

    top = candidates[:top_n]
    top_board = render_card_block(top, header="WNBA PLAYER-PROPS PARLAY")
    # Combined-ticket CLV snapshot via the shared MLB helper.
    from edge_equation.engines.mlb.game_results_parlay import (
        log_parlay_clv_snapshot,
    )
    log_parlay_clv_snapshot(
        candidates=top, universe="wnba_player_props", target_date=target,
    )

    return WNBAPlayerPropsParlayCard(
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
    header: str = "WNBA PLAYER-PROPS PARLAY",
    note: str = PARLAY_CARD_NOTE,
    transparency_note: str = PARLAY_TRANSPARENCY_NOTE,
) -> str:
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
# Engine class — registry façade
# ---------------------------------------------------------------------------


@dataclass
class WNBAPlayerPropsParlayEngine:
    rules: WNBAParlayRules = WNBA_PARLAY_RULES
    top_n: int = 3
    name: str = "wnba_player_props_parlay"

    def run(
        self, *,
        wnba_prop_outputs: Sequence = (),
        target_date: Optional[str] = None,
    ) -> WNBAPlayerPropsParlayCard:
        try:
            return build_player_props_parlay(
                wnba_prop_outputs=wnba_prop_outputs,
                target_date=target_date,
                rules=self.rules,
                top_n=self.top_n,
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                "WNBAPlayerPropsParlayEngine: build failed (%s): %s",
                type(e).__name__, e,
            )
            return WNBAPlayerPropsParlayCard(
                target_date=target_date or _date.today().isoformat(),
                explanation=(
                    f"{NO_QUALIFIED_PARLAY_MESSAGE} (build error: "
                    f"{type(e).__name__})"
                ),
            )

    @staticmethod
    def joint_probability(
        legs: Sequence[ParlayLeg], *,
        rules: WNBAParlayRules = WNBA_PARLAY_RULES,
    ) -> float:
        return simulate_correlated_joint_prob(
            legs,
            n_trials=rules.mc_trials,
            seed=rules.mc_seed,
            max_abs_correlation=rules.max_abs_correlation,
        )
