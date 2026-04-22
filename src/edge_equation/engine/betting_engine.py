"""
Betting engine.

Glue layer that takes a FeatureBundle + market Line and produces a Pick.
"""
from decimal import Decimal
from typing import Optional

from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.scoring import ConfidenceScorer
from edge_equation.engine.feature_builder import (
    FeatureBundle,
    META_DECAY_HALFLIFE_KEY,
    META_HFA_VALUE_KEY,
)
from edge_equation.engine.major_variance import (
    detect as detect_major_variance,
    tag_pick as tag_major_variance,
)
from edge_equation.engine.pick_schema import Pick, Line
from edge_equation.utils.logging import get_logger


_logger = get_logger("edge-equation.engine")

PROB_MARKETS = {"ML", "Run_Line", "Puck_Line", "Spread", "BTTS"}
EXPECTATION_MARKETS = {
    "Total", "Game_Total",
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
}

# Phase 28 sanity guard. ProbabilityCalculator clamps fair_prob to
# [0.01, 0.99]; combined with even reasonable American odds, an honest
# edge above 30% is essentially impossible. A reading higher than this
# means an upstream input is wrong (missing strength data, mislabeled
# selection side, etc.) -- treat the pick as ungradeable rather than
# publish a "+48% on +2200" absurdity.
_MAX_REASONABLE_EDGE = Decimal("0.30")


def _resolve_selection_side(
    market_type: str,
    selection: str,
    home_team: str,
    away_team: str,
) -> Optional[str]:
    """Identify which side of the market this pick is on so the engine
    can flip ProbabilityCalculator's home-centric fair_prob when needed.

    ML: returns 'home' iff selection matches home_team, 'away' iff it
    matches away_team, else None (refuse to grade).
    BTTS: 'home' for Yes (matches the "fair_prob" computed by the
    Poisson math), 'away' for No.
    Other PROB_MARKETS (Run_Line / Puck_Line / Spread): not yet
    supported by ProbabilityCalculator -- those raise upstream and
    never reach this function.
    """
    if not selection:
        return None
    sel = selection.strip()
    if market_type == "ML":
        if home_team and sel == home_team:
            return "home"
        if away_team and sel == away_team:
            return "away"
        return None
    if market_type == "BTTS":
        s = sel.lower()
        if s in ("yes",):
            return "home"
        if s in ("no",):
            return "away"
        return None
    return "home"


def _baseline_read(
    market_type: str,
    selection: str,
    bundle: FeatureBundle,
    fair_prob: Optional[Decimal],
    edge: Optional[Decimal],
    hfa_value: Optional[Decimal],
    decay_halflife_days: Optional[Decimal],
) -> str:
    """Compose a factual one-line read from whatever feature inputs are
    on hand. Premium subscribers see this verbatim under "Read:" --
    Facts Not Feelings, no hype, no tout language. Returns "" when
    we can't say anything honest."""
    bits = []
    inputs = bundle.inputs or {}
    if market_type == "ML":
        sh = inputs.get("strength_home")
        sa = inputs.get("strength_away")
        if sh is not None and sa is not None:
            try:
                diff = float(sh) - float(sa)
                if abs(diff) > 0.10:
                    side = "home" if diff > 0 else "away"
                    bits.append(
                        f"Composer strength differential favors {side} "
                        f"({float(sh):.2f} vs {float(sa):.2f})."
                    )
            except (TypeError, ValueError):
                pass
    if market_type in ("Total", "Game_Total"):
        pace = inputs.get("pace")
        off = inputs.get("off_env")
        if pace is not None and off is not None:
            try:
                bits.append(
                    f"Run environment pace={float(pace):.2f} "
                    f"off={float(off):.2f}."
                )
            except (TypeError, ValueError):
                pass
    if hfa_value is not None:
        try:
            sign = "+" if float(hfa_value) >= 0 else ""
            bits.append(f"Home-field adjustment {sign}{float(hfa_value):.3f}.")
        except (TypeError, ValueError):
            pass
    if decay_halflife_days is not None:
        try:
            bits.append(f"Form decay tau/2 {float(decay_halflife_days):.0f}d.")
        except (TypeError, ValueError):
            pass
    if not bits and fair_prob is not None and edge is not None:
        bits.append(
            "Edge derived from price/probability delta vs market consensus."
        )
    return " ".join(bits)


class BettingEngine:

    @staticmethod
    def evaluate(
        bundle: FeatureBundle,
        line: Line,
        public_mode: bool = False,
        mc_stability: Optional[dict] = None,
    ) -> Pick:
        market = bundle.market_type
        sport = bundle.sport
        selection = bundle.selection or ""

        fv = ProbabilityCalculator.calculate_fair_value(
            market, sport, bundle.inputs, bundle.universal_features
        )

        fair_prob: Optional[Decimal] = None
        expected_value: Optional[Decimal] = None
        edge: Optional[Decimal] = None
        kelly: Optional[Decimal] = None
        grade = "C"
        realization = 47
        sanity_reason: Optional[str] = None

        if market in PROB_MARKETS:
            fair_prob = fv.get("fair_prob")
            # ----------------------------------------------------------
            # Phase 28 critical fix: ProbabilityCalculator returns the
            # HOME team's win probability (or BTTS "Yes" probability) by
            # construction. If the SELECTION is the away team / "No",
            # we MUST mirror the probability before computing edge --
            # otherwise both sides of the same game get graded with
            # the same overstated fair_prob, which is the +48%-on-+2200
            # bug pattern we just shipped a fix for.
            # ----------------------------------------------------------
            if fair_prob is not None:
                home_team = bundle.metadata.get("home_team", "")
                away_team = bundle.metadata.get("away_team", "")
                side = _resolve_selection_side(
                    market, selection, home_team, away_team,
                )
                if side is None:
                    # Selection doesn't match a known side. Don't bluff a
                    # number; leave the pick ungradeable so it stays out
                    # of the public feed.
                    sanity_reason = (
                        f"selection {selection!r} matches neither home "
                        f"({home_team!r}) nor away ({away_team!r})"
                    )
                    fair_prob = None
                elif side == "away":
                    # Mirror around 0.5 so this side's fair_prob is the
                    # complement of the home/Yes-side probability.
                    fair_prob = (Decimal("1") - fair_prob).quantize(
                        Decimal("0.000001")
                    )

            calib = EVCalculator.calibrate(
                public_mode,
                {"fair_prob": fair_prob},
                {"odds": line.odds},
            )
            edge = calib["edge"]
            kelly = calib["kelly"]

            # ----------------------------------------------------------
            # Sanity guard: a POSITIVE edge above 30% on a binary
            # market is essentially impossible at honest market
            # consensus prices and is the diagnostic signature of the
            # both-sides-A+ overconfidence bug. Reject rather than
            # publish absurdity. Large NEGATIVE edges (-0.33 etc.)
            # are legitimate "this side is overpriced" signals -- let
            # them through so they grade D/F via ConfidenceScorer and
            # stay out of the A+/A free-content tier.
            # ----------------------------------------------------------
            if edge is not None and edge > _MAX_REASONABLE_EDGE:
                sanity_reason = (
                    f"edge={edge} exceeds +{_MAX_REASONABLE_EDGE} sanity "
                    f"ceiling on {market} (likely overconfident inputs)"
                )
                _logger.warning(
                    f"BettingEngine: rejecting impossible edge -- "
                    f"sport={sport} market={market} selection={selection!r} "
                    f"odds={line.odds} fair_prob={fair_prob} edge={edge}. "
                    f"{sanity_reason}"
                )
                edge = None
                kelly = None
                grade = "C"
                realization = 47
            elif not public_mode and edge is not None:
                grade = ConfidenceScorer.grade(edge)
                realization = ConfidenceScorer.realization_for_grade(grade)

        elif market in EXPECTATION_MARKETS:
            if "expected_total" in fv:
                expected_value = fv["expected_total"]
            elif "expected_value" in fv:
                expected_value = fv["expected_value"]
            edge = None
            kelly = None

        else:
            raise ValueError(f"BettingEngine: unsupported market {market}")

        halflife_raw = bundle.metadata.get(META_DECAY_HALFLIFE_KEY)
        hfa_raw = bundle.metadata.get(META_HFA_VALUE_KEY)
        decay_halflife_days = Decimal(halflife_raw) if halflife_raw is not None else None
        hfa_value = Decimal(hfa_raw) if hfa_raw is not None else None

        # Auto-populate Read field when upstream didn't supply one.
        # Premium subscribers see this string verbatim under "Read:".
        existing_read = (bundle.metadata or {}).get("read_notes") or ""
        if not existing_read:
            existing_read = _baseline_read(
                market_type=market,
                selection=selection,
                bundle=bundle,
                fair_prob=fair_prob,
                edge=edge,
                hfa_value=hfa_value,
                decay_halflife_days=decay_halflife_days,
            )

        meta = {
            "raw_universal_sum": str(fv.get("raw_universal_sum"))
                if fv.get("raw_universal_sum") is not None else None,
            # Premium "why this pick" audit trail: the exact numeric
            # feature inputs the engine consumed to produce this
            # projection. Stashed verbatim (as stringified Decimals)
            # so the posting renderer can surface them. Free content
            # strips this via PublicModeSanitizer; premium keeps it.
            "feature_inputs": {
                **{k: str(v) for k, v in (bundle.inputs or {}).items()},
                **{k: str(v) for k, v in (bundle.universal_features or {}).items()},
            },
            **dict(bundle.metadata),
        }
        if existing_read:
            meta["read_notes"] = existing_read
        if sanity_reason:
            meta["sanity_rejected_reason"] = sanity_reason

        pick = Pick(
            sport=sport,
            market_type=market,
            selection=selection,
            line=line,
            fair_prob=fair_prob,
            expected_value=expected_value,
            edge=edge,
            kelly=kelly,
            grade=grade,
            realization=realization,
            game_id=bundle.game_id,
            event_time=bundle.event_time,
            decay_halflife_days=decay_halflife_days,
            hfa_value=hfa_value,
            metadata=meta,
        )
        # Major Variance Signal: runs in premium mode only. The detector
        # is credibility-first -- if mc_stability is missing the signal
        # silently does NOT fire. We still tag the reason into metadata
        # so an auditor can see why.
        if not public_mode:
            signal = detect_major_variance(pick, mc_stability=mc_stability)
            pick = tag_major_variance(pick, signal)
        return pick
