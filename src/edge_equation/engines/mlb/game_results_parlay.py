"""MLB game-results parlay engine — strict 3–6 leg builder.

This is the first of two new MLB-only parlay engines. It pulls today's
qualifying game-level picks out of the existing engines (NRFI/YRFI,
full-game ML / Run_Line / Total / Team_Total / F5_Total / F5_ML),
filters them through the strict ``MLBParlayRules`` gate, and asks the
shared ``engines.parlay`` builder to assemble candidate combos with
correlation-adjusted Monte-Carlo joint probability + EV gating.

What the engine WILL build:
* Tickets of 3–6 legs.
* Tickets where every leg either clears 4pp edge against the de-vigged
  closing line OR is classified ELITE (Signal Elite / LOCK).
* Tickets whose correlation-adjusted joint probability still produces
  positive expected value AFTER vig.

What the engine WILL NOT build:
* 2-leg tickets, 7+ leg tickets, or "lottos" of any size.
* Tickets that include a leg from a market this universe doesn't
  finalize (e.g. a player prop accidentally falling into this
  universe).
* Tickets where any leg fails the ELITE-or-4pp gate.
* Tickets whose post-vig EV is non-positive.

If no combination passes, the engine emits a single explanatory pick
("No qualified parlay today …"), never a forced ticket. That's the
"data does not support a high-confidence combination" branch from the
audit.
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

from .thresholds import (
    MLB_PARLAY_RULES,
    MLBParlayRules,
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enriched leg wrapper
#
# `ParlayLeg` is intentionally a frozen dataclass — its identity hashes
# into the parlay-correlation lookup, and the shared builder treats it
# as immutable. We carry the strict-policy metadata (edge, confidence,
# CLV) on a separate `EnrichedLeg` wrapper so the gate filter can read
# them in one place. The wrapper unwraps cleanly via `.leg` when it's
# time to hand the surviving pool to the shared builder.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnrichedLeg:
    """A `ParlayLeg` plus the per-leg strict-policy metadata."""
    leg: ParlayLeg
    edge_frac: float = 0.0       # signed edge against de-vigged market
    confidence: float = 0.30     # underlying model confidence
    clv_pp: float = 0.0          # closing-line value, percentage points

    @property
    def market_type(self) -> str:
        return self.leg.market_type

    @property
    def tier(self) -> Tier:
        return self.leg.tier


# ---------------------------------------------------------------------------
# Daily card payload
# ---------------------------------------------------------------------------


@dataclass
class GameResultsParlayCard:
    """The card returned by the engine — packaged for the daily runner.

    Mirrors the `*Card` shape used by every other MLB engine
    (`PropsCard`, `FullGameCard`, NRFI's email card) so the unified
    runner can plug it in without a special case.
    """

    target_date: str
    candidates: list[ParlayCandidate] = field(default_factory=list)
    n_legs_pool: int = 0
    n_legs_after_gate: int = 0
    explanation: str = ""
    top_board_text: str = ""
    note: str = PARLAY_CARD_NOTE

    @property
    def has_qualified(self) -> bool:
        return bool(self.candidates)


# ---------------------------------------------------------------------------
# Leg construction adapters
# ---------------------------------------------------------------------------


def _full_game_to_enriched(
    out, *, rules: MLBParlayRules,
) -> Optional[EnrichedLeg]:
    """Adapt a `full_game.output.FullGameOutput` to an `EnrichedLeg`.

    Returns None when the row's market isn't allowed in this universe.
    The strict gate (edge / tier / confidence) is applied later by
    ``filter_legs_by_strict_rules`` so the caller can audit how many
    legs each filter step removed.
    """
    market = str(getattr(out, "market_type", "") or "")
    if market not in rules.allowed_game_result_markets:
        return None

    side = str(getattr(out, "side", "") or "")
    team = str(getattr(out, "team_tricode", "") or "")
    line_value = getattr(out, "line_value", None)
    label = _label_for_full_game(market, side, team, line_value)

    try:
        tier = Tier(str(getattr(out, "tier", "") or "NO_PLAY").upper())
    except ValueError:
        tier = Tier.NO_PLAY

    leg = ParlayLeg(
        market_type=market,
        side=_side_for_full_game(market, side, team, line_value),
        side_probability=float(getattr(out, "model_prob", 0.0) or 0.0),
        american_odds=float(getattr(out, "american_odds", -110.0) or -110.0),
        tier=tier,
        game_id=str(getattr(out, "event_id", "") or ""),
        player_id=None,
        label=label,
    )
    edge_pp = float(getattr(out, "edge_pp", 0.0) or 0.0)
    return EnrichedLeg(
        leg=leg,
        edge_frac=float(edge_pp / 100.0),
        confidence=float(getattr(out, "confidence", 0.30) or 0.30),
        clv_pp=float(getattr(out, "clv_pp", 0.0) or 0.0),
    )


def _nrfi_to_enriched(
    row, *, rules: MLBParlayRules,
) -> Optional[EnrichedLeg]:
    """Adapt an NRFI/YRFI prediction row dict to an `EnrichedLeg`.

    The NRFI engine writes one row per game with the NRFI-side
    probability; the caller decides whether to stake the NRFI or the
    YRFI side. ``row`` here is the dict shape produced by
    ``nrfi.run_daily.main`` (or the email-report bridge): ``game_pk``,
    ``nrfi_prob``, ``market_prob``, plus optional tier metadata.
    """
    side_market = str(row.get("market_type") or row.get("selection") or "NRFI")
    if side_market not in rules.allowed_game_result_markets:
        return None
    if side_market not in {"NRFI", "YRFI"}:
        return None

    nrfi_prob = float(row.get("nrfi_prob", 0.0) or 0.0)
    if side_market == "NRFI":
        side_prob = nrfi_prob
        side_label = "Under 0.5"
    else:
        side_prob = 1.0 - nrfi_prob
        side_label = "Over 0.5"

    market_prob = row.get("market_prob")
    if market_prob is None:
        edge_frac = 0.0
        american = -110.0
    else:
        try:
            mp = float(market_prob)
        except (TypeError, ValueError):
            mp = 0.0
        if mp <= 0 or mp >= 1:
            edge_frac = 0.0
            american = -110.0
        else:
            edge_frac = side_prob - mp
            if mp >= 0.5:
                american = round(-100.0 * mp / max(1e-9, 1.0 - mp))
            else:
                american = round(100.0 * (1.0 - mp) / max(1e-9, mp))

    from edge_equation.engines.tiering import classify_tier
    clf = classify_tier(market_type=side_market, side_probability=side_prob)
    tier = clf.tier

    label = (
        f"{side_market} ({row.get('away_team', '')} @ "
        f"{row.get('home_team', '')})"
    ).strip()

    leg = ParlayLeg(
        market_type=side_market,
        side=side_label,
        side_probability=float(side_prob),
        american_odds=float(american),
        tier=tier,
        game_id=str(row.get("game_pk") or row.get("game_id") or ""),
        player_id=None,
        label=label or side_market,
    )
    confidence = float(row.get("confidence", 0.65) or 0.65)
    clv_pp = float(row.get("clv_pp", 0.0) or 0.0)
    return EnrichedLeg(
        leg=leg, edge_frac=float(edge_frac),
        confidence=confidence, clv_pp=clv_pp,
    )


def _side_for_full_game(
    market: str, side: str, team: str, line_value,
) -> str:
    """Normalize the side string the way the parlay correlation table
    expects (``Over 8.5``, ``NYY ML``, ``BOS -1.5``)."""
    side_l = (side or "").lower()
    if market in ("ML", "F5_ML"):
        return f"{team or side} ML"
    if market == "Run_Line":
        if line_value is None:
            return f"{team or side} RL"
        return f"{team or side} {line_value:+g}"
    if line_value is None:
        return side or "Over"
    direction = "Over" if "over" in side_l else "Under"
    return f"{direction} {line_value:g}"


def _label_for_full_game(
    market: str, side: str, team: str, line_value,
) -> str:
    """Operator-readable label rendered in the parlay card."""
    if market in ("ML", "F5_ML"):
        suffix = " (F5)" if market == "F5_ML" else ""
        return f"{team or side}{suffix} ML"
    if market == "Run_Line":
        line_str = "" if line_value is None else f" {line_value:+g}"
        return f"{team or side} RL{line_str}"
    if market == "Team_Total":
        line_str = "" if line_value is None else f" {line_value:g}"
        direction = side.title() if side else "Over"
        return f"{team} Team Total {direction}{line_str}"
    if market in ("Total", "F5_Total"):
        prefix = "F5 " if market == "F5_Total" else ""
        line_str = "" if line_value is None else f" {line_value:g}"
        direction = side.title() if side else "Over"
        return f"{prefix}Total {direction}{line_str}"
    return f"{market} {side}".strip()


# ---------------------------------------------------------------------------
# Public leg builder
# ---------------------------------------------------------------------------


def build_game_results_legs(
    *,
    full_game_outputs: Sequence = (),
    nrfi_rows: Sequence[dict] = (),
    rules: MLBParlayRules = MLB_PARLAY_RULES,
) -> list[EnrichedLeg]:
    """Convert today's per-engine outputs into the leg pool.

    The two source streams are:
    * ``full_game_outputs``: an iterable of ``FullGameOutput`` (or a
      duck-typed equivalent — only the named attrs are read).
    * ``nrfi_rows``: dicts shaped like the NRFI predictions table.

    Returns one ``EnrichedLeg`` per qualifying side. Legs that don't
    belong to this universe are silently skipped.
    """
    legs: list[EnrichedLeg] = []
    for out in full_game_outputs:
        leg = _full_game_to_enriched(out, rules=rules)
        if leg is not None:
            legs.append(leg)
    for row in nrfi_rows:
        leg = _nrfi_to_enriched(row, rules=rules)
        if leg is not None:
            legs.append(leg)
    return legs


# ---------------------------------------------------------------------------
# Strict-policy filter
# ---------------------------------------------------------------------------


def filter_legs_by_strict_rules(
    legs: Iterable[EnrichedLeg], *,
    rules: MLBParlayRules = MLB_PARLAY_RULES,
    market_universe: str = "game_results",
) -> list[EnrichedLeg]:
    """Drop legs that don't satisfy the strict per-leg gate."""
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
# Main builder — strict-policy parlay candidates
# ---------------------------------------------------------------------------


def build_game_results_parlay(
    *,
    full_game_outputs: Sequence = (),
    nrfi_rows: Sequence[dict] = (),
    target_date: Optional[str] = None,
    rules: MLBParlayRules = MLB_PARLAY_RULES,
    top_n: int = 3,
) -> GameResultsParlayCard:
    """Build the ``GameResultsParlayCard`` for ``target_date``.

    Pipeline:
    1. Convert the day's full-game + NRFI outputs into ``EnrichedLeg``s.
    2. Filter to legs that pass the strict per-leg gate (4pp edge OR
       ELITE tier, plus confidence + CLV floors).
    3. Hand the surviving pool to the shared parlay builder with
       ``min_legs=3``, ``max_legs=6``, joint-prob + EV floors per the
       audit rules. The shared builder also rejects mutually-exclusive
       combos (e.g., NRFI + YRFI on the same game).
    4. If anything qualifies, render the top-N candidates by EV. If
       nothing qualifies, the explanation text is the audit's
       "No qualified parlay today …" message.
    """
    target = target_date or _date.today().isoformat()
    pool = build_game_results_legs(
        full_game_outputs=full_game_outputs,
        nrfi_rows=nrfi_rows,
        rules=rules,
    )
    n_pool = len(pool)
    qualifying = filter_legs_by_strict_rules(
        pool, rules=rules, market_universe="game_results",
    )
    n_after = len(qualifying)
    log.info(
        "MLB game-results parlay: %d/%d legs cleared the strict gate",
        n_after, n_pool,
    )

    candidates: list[ParlayCandidate] = []
    if n_after >= rules.min_legs:
        # Adapt the strict rules into the shared `ParlayConfig`. Set
        # ``min_tier`` to LEAN so the shared builder doesn't re-filter
        # legs we've already pre-screened — our gate is stricter.
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
        # Enforce the audit's MIN_LEGS floor here (shared builder's
        # minimum is 2 by default).
        candidates = [
            c for c in all_candidates if c.n_legs >= rules.min_legs
        ]
    explanation = (
        ""
        if candidates else
        NO_QUALIFIED_PARLAY_MESSAGE
    )

    top = candidates[:top_n]
    top_board = render_card_block(top, header="GAME-RESULTS PARLAY")

    return GameResultsParlayCard(
        target_date=target,
        candidates=top,
        n_legs_pool=n_pool,
        n_legs_after_gate=n_after,
        explanation=explanation,
        top_board_text=top_board,
    )


# ---------------------------------------------------------------------------
# Renderer (mirrors NRFI / Props / Full-Game TOP BOARD format)
# ---------------------------------------------------------------------------


def render_card_block(
    candidates: Sequence[ParlayCandidate], *,
    header: str = "GAME-RESULTS PARLAY",
    note: str = PARLAY_CARD_NOTE,
) -> str:
    """Plain-text block ready to drop into the daily email."""
    if not candidates:
        return (
            f"{header}\n"
            f"{'═' * 60}\n"
            f"  {NO_QUALIFIED_PARLAY_MESSAGE}\n"
        )
    top = list(candidates)
    out_lines = [
        f"{header} — Top {len(top)} qualified ticket(s)",
        f"  Note: {note}",
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
class MLBGameResultsParlayEngine:
    """The class the central engine_registry hands to the daily runner.

    Stateless on purpose — all configuration flows through the
    ``MLBParlayRules`` instance and the source dataframes the daily
    runner already loaded for the per-market engines. Keeping the
    engine stateless means a single subprocess can run NRFI, full-game,
    props, and both parlay engines in series without any cross-engine
    side effects.
    """

    rules: MLBParlayRules = MLB_PARLAY_RULES
    top_n: int = 3

    name: str = "mlb_game_results_parlay"

    def run(
        self, *,
        full_game_outputs: Sequence = (),
        nrfi_rows: Sequence[dict] = (),
        target_date: Optional[str] = None,
    ) -> GameResultsParlayCard:
        """Build today's card and return it. Never raises."""
        try:
            return build_game_results_parlay(
                full_game_outputs=full_game_outputs,
                nrfi_rows=nrfi_rows,
                target_date=target_date,
                rules=self.rules,
                top_n=self.top_n,
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                "MLBGameResultsParlayEngine: build failed (%s): %s",
                type(e).__name__, e,
            )
            return GameResultsParlayCard(
                target_date=target_date or _date.today().isoformat(),
                explanation=(
                    f"{NO_QUALIFIED_PARLAY_MESSAGE} (build error: "
                    f"{type(e).__name__})"
                ),
            )

    @staticmethod
    def joint_probability(legs: Sequence[ParlayLeg], *,
                          rules: MLBParlayRules = MLB_PARLAY_RULES) -> float:
        """Surface the correlation-adjusted joint probability MC for a
        given set of legs — used by the backtest's calibration check."""
        return simulate_correlated_joint_prob(
            legs,
            n_trials=rules.mc_trials,
            seed=rules.mc_seed,
            max_abs_correlation=rules.max_abs_correlation,
        )
