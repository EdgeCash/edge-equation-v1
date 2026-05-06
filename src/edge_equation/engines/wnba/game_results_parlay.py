"""WNBA game-results parlay engine — strict 3–6 leg builder.

Mirrors `engines.mlb.game_results_parlay` exactly. The only WNBA-
specific surface area is the leg adapter, which translates a WNBA
`Output` row (or any duck-typed equivalent) into a `ParlayLeg` the
shared `engines.parlay` builder can combine.

Strict policy comes from `engines.wnba.thresholds.WNBA_PARLAY_RULES`,
which itself imports the audit-locked numerics from
`engines.mlb.thresholds` so the two sports never drift apart on
3-6 legs / ≥4pp edge / ELITE bypass / EV>0 after vig.
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
class WNBAGameResultsParlayCard:
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
# Leg adapter — WNBA `Output` → `EnrichedLeg`
# ---------------------------------------------------------------------------


def _market_str(market) -> str:
    """Coerce the WNBA Market enum (or string) to its canonical name."""
    if hasattr(market, "value"):
        return str(market.value)
    return str(market or "")


def _wnba_to_enriched(
    out, *, rules: WNBAParlayRules,
) -> Optional[EnrichedLeg]:
    """Adapt a WNBA `Output` (game-results market) to an `EnrichedLeg`.

    Only the named attributes are read — any duck-typed object with
    the same shape works (used by the backtest harness and tests).
    """
    market = _market_str(getattr(out, "market", "") or "")
    if market not in rules.allowed_game_result_markets:
        return None

    team = str(getattr(out, "team", "") or "")
    opponent = str(getattr(out, "opponent", "") or "")
    line = float(getattr(out, "line", 0.0) or 0.0)
    side = _side_for_market(out, market, team, line)
    label = _label_for_market(market, team, opponent, line, side)

    # Edge percentage on the WNBA Output is in points (proj − line).
    # Translate to a fraction of probability the way the MLB engine
    # treats `edge_pp / 100`. WNBA `probability` is the model's side
    # probability so the shape lines up directly.
    prob = float(getattr(out, "probability", 0.0) or 0.0)
    edge_pp = float(getattr(out, "edge_pp", 0.0) or 0.0)
    if not edge_pp:
        # Older WNBA Outputs don't expose edge_pp directly; estimate
        # from probability vs the standard -110 implied 52.4%.
        edge_pp = (prob - 0.524) * 100.0
    edge_frac = edge_pp / 100.0

    confidence = float(getattr(out, "confidence", 0.30) or 0.30)
    clv_pp = float(getattr(out, "clv_pp", 0.0) or 0.0)

    # Derive the conviction tier from edge — same ladder MLB uses.
    try:
        clf = classify_tier(
            market_type=market, edge=edge_frac, side_probability=prob,
        )
        tier = clf.tier
    except Exception:
        tier = Tier.NO_PLAY

    american = float(getattr(out, "american_odds", 0.0) or 0.0)
    if not american:
        # WNBA outputs typically only carry decimal_odds in the meta
        # bag; reach into it as a soft fallback.
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
        game_id=str(getattr(out, "game_id", "") or f"{team}@{opponent}"),
        player_id=None,
        label=label,
    )
    return EnrichedLeg(
        leg=leg,
        edge_frac=float(edge_frac),
        confidence=confidence,
        clv_pp=clv_pp,
    )


def _side_for_market(out, market: str, team: str, line: float) -> str:
    """Normalize the side string for the parlay correlation table."""
    direction = str(getattr(out, "side", "") or "").lower()
    if market == "fullgame_ml":
        return f"{team} ML"
    if market == "fullgame_spread":
        signed = f"{line:+g}" if line else ""
        return f"{team} {signed}".strip()
    if market in ("fullgame_total", "team_total"):
        d = "Over" if "over" in direction or direction == "" else "Under"
        return f"{d} {line:g}"
    return direction or "Over"


def _label_for_market(
    market: str, team: str, opponent: str, line: float, side: str,
) -> str:
    matchup = f"{team} vs {opponent}".strip(" vs ").strip()
    if market == "fullgame_ml":
        return f"{team} ML ({matchup})"
    if market == "fullgame_spread":
        signed = f"{line:+g}" if line else ""
        return f"{team} Spread {signed} ({matchup})".strip()
    if market == "team_total":
        return f"{team} Team Total {side} ({matchup})".strip()
    if market == "fullgame_total":
        return f"Total {side} ({matchup})".strip()
    return f"{market} {side}".strip()


# ---------------------------------------------------------------------------
# Public leg builder + filter
# ---------------------------------------------------------------------------


def build_game_results_legs(
    *,
    wnba_outputs: Sequence = (),
    rules: WNBAParlayRules = WNBA_PARLAY_RULES,
) -> list[EnrichedLeg]:
    """Convert today's WNBA game-result outputs into the leg pool.

    Drops outputs whose market isn't in the allowed game-results
    universe (e.g. player-prop rows that share the same `Output`
    type but belong to the props universe).
    """
    legs: list[EnrichedLeg] = []
    for out in wnba_outputs:
        leg = _wnba_to_enriched(out, rules=rules)
        if leg is not None:
            legs.append(leg)
    return legs


def filter_legs_by_strict_rules(
    legs: Iterable[EnrichedLeg], *,
    rules: WNBAParlayRules = WNBA_PARLAY_RULES,
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
            market_universe="game_results",
        ):
            out.append(enriched)
    return out


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------


def build_game_results_parlay(
    *,
    wnba_outputs: Sequence = (),
    target_date: Optional[str] = None,
    rules: WNBAParlayRules = WNBA_PARLAY_RULES,
    top_n: int = 3,
) -> WNBAGameResultsParlayCard:
    """Build the WNBA game-results parlay card for ``target_date``."""
    target = target_date or _date.today().isoformat()
    pool = build_game_results_legs(wnba_outputs=wnba_outputs, rules=rules)
    n_pool = len(pool)
    qualifying = filter_legs_by_strict_rules(pool, rules=rules)
    n_after = len(qualifying)
    log.info(
        "WNBA game-results parlay: %d/%d legs cleared the strict gate",
        n_after, n_pool,
    )
    # CLV snapshot: each leg's per-side CLV is logged upstream by the
    # WNBA closing-line snapshot job (mirrors MLB's flow). The
    # combined-ticket snapshot is written below via the shared
    # `log_parlay_clv_snapshot()` helper.

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
    top_board = render_card_block(top, header="WNBA GAME-RESULTS PARLAY")
    # Combined-ticket CLV snapshot — re-uses the MLB helper so the
    # ClvTracker plumbing stays in one place.
    from edge_equation.engines.mlb.game_results_parlay import (
        log_parlay_clv_snapshot,
    )
    log_parlay_clv_snapshot(
        candidates=top, universe="wnba_game_results", target_date=target,
    )

    return WNBAGameResultsParlayCard(
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
    header: str = "WNBA GAME-RESULTS PARLAY",
    note: str = PARLAY_CARD_NOTE,
    transparency_note: str = PARLAY_TRANSPARENCY_NOTE,
) -> str:
    """Plain-text block ready to drop into the daily card."""
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
class WNBAGameResultsParlayEngine:
    rules: WNBAParlayRules = WNBA_PARLAY_RULES
    top_n: int = 3
    name: str = "wnba_game_results_parlay"

    def run(
        self, *,
        wnba_outputs: Sequence = (),
        target_date: Optional[str] = None,
    ) -> WNBAGameResultsParlayCard:
        try:
            return build_game_results_parlay(
                wnba_outputs=wnba_outputs,
                target_date=target_date,
                rules=self.rules,
                top_n=self.top_n,
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                "WNBAGameResultsParlayEngine: build failed (%s): %s",
                type(e).__name__, e,
            )
            return WNBAGameResultsParlayCard(
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
