"""
Betting engine.

Glue layer that takes a FeatureBundle + market Line and produces a Pick.
"""
from decimal import Decimal
from typing import List, Optional

from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.monte_carlo import MonteCarloSimulator
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
FIRST_INNING_MARKETS = {"NRFI", "YRFI"}
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

# Confidence penalty: Bradley-Terry team strengths are derived from
# settled-game history. Below a meaningful sample on either team, the
# strength estimate is noise and any "edge" the engine claims is a
# cold-start artifact rather than a real signal. We cap the grade at
# C in that regime so picks earn confidence with data instead of
# getting it by default. Applies only to PROB_MARKETS where strength
# drives fair_prob (ML, Spread, Run_Line, Puck_Line) -- not BTTS
# (Poisson lambdas), NRFI/YRFI (first-inning lambdas), totals, or
# rate-prop markets, which derive from different inputs.
#
# Threshold = 10 because FeatureComposer's games_used_home/away
# counters are capped at SportConfig.form_window_games (MLB 15, NHL
# 10, NBA 10, NFL 5). A threshold higher than the form window is
# unreachable -- the original threshold of 20 made every pick C even
# when MLB had 30+ games per team. 10 means "both teams have at
# least 10 recent games of evidence" which is meaningful and
# reachable for sports with form_window >= 10. NFL stays capped
# indefinitely (form_window 5 < 10) but that's correct given how
# little NFL data flows in the offseason. Future cleanup: have
# composer expose a separate season-total counter so the threshold
# can be a true "games this season" check decoupled from the form
# window.
_MIN_CONFIDENT_GAMES_USED = 10
_STRENGTH_DRIVEN_MARKETS = {"ML", "Spread", "Run_Line", "Puck_Line"}


def _resolve_selection_side(
    market_type: str,
    selection: str,
    home_team: str,
    away_team: str,
) -> Optional[str]:
    """Identify which side of the market this pick is on so the engine
    can flip ProbabilityCalculator's home-centric fair_prob when needed.

    ML / Spread / Run_Line / Puck_Line: returns 'home' iff selection
    matches home_team, 'away' iff it matches away_team, else None
    (refuse to grade). The math layer returns a home-centric fair_prob
    for all four market types, so an away-side pick must be mirrored.
    BTTS: 'home' for Yes (matches the "fair_prob" computed by the
    Poisson math), 'away' for No.
    """
    if not selection:
        return None
    sel = selection.strip()
    if market_type in ("ML", "Spread", "Run_Line", "Puck_Line"):
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
    return None


def _baseline_read(
    market_type: str,
    selection: str,
    bundle: FeatureBundle,
    fair_prob: Optional[Decimal],
    edge: Optional[Decimal],
    hfa_value: Optional[Decimal],
    decay_halflife_days: Optional[Decimal],
    mc_stability: Optional[dict] = None,
) -> str:
    """Phase 31: substantive Read built from FACTUAL evidence stashed by
    the composer. Premium subscribers see this verbatim under "Read:".

    Brand rules:
      * Facts Not Feelings -- every sentence quotes a number or a
        verifiable string (W/L record, Elo rating, weather mph).
      * No tout language. No "lock", no "value play", no "edge of the
        day" filler.
      * No generic fallback prose. If the only thing we know is that
        the engine produced an edge, we say nothing -- the math line
        in the rendered card already carries that signal. Empty Read
        beats hype Read.

    The function consumes (in order of preference, every key optional):
      bundle.metadata['read_context']  -- composer's structured evidence
      bundle.metadata['pitching_*']    -- starter/bullpen FIP if present
      bundle.metadata['weather']       -- {wind_mph, temp_f, dome}
      bundle.metadata['umpire']        -- {name, k_factor}
      bundle.metadata['rest_days_*']   -- ints
      bundle.metadata['travel_miles_*']-- ints
      bundle.inputs['pace'/'off_env'/'def_env']  -- totals env factors
      mc_stability                     -- engine's 10k MC band
    """
    bits: List[str] = []
    inputs = bundle.inputs or {}
    meta = bundle.metadata or {}
    rc: dict = meta.get("read_context") or {}

    # ---------------------------------------------------------------
    # ML markets: lead with team form / Elo. The composer stash gives
    # the reader an immediate "what's behind this projection" anchor.
    # ---------------------------------------------------------------
    if market_type == "ML":
        rfh = rc.get("recent_form_home")
        rfa = rc.get("recent_form_away")
        rd_h = rc.get("run_diff_home")
        rd_a = rc.get("run_diff_away")
        if rfh and rfa:
            sign_h = "+" if (rd_h or 0) >= 0 else ""
            sign_a = "+" if (rd_a or 0) >= 0 else ""
            rd_part = ""
            if rd_h is not None and rd_a is not None:
                rd_part = f" (run diff {sign_h}{rd_h} vs {sign_a}{rd_a})"
            bits.append(f"Recent form home {rfh}, away {rfa}{rd_part}.")
        elo_diff = rc.get("elo_diff")
        if elo_diff is None:
            elo_diff = meta.get("elo_diff")
        if elo_diff is not None:
            try:
                ed = int(elo_diff)
                if abs(ed) >= 25:
                    side = "home" if ed > 0 else "away"
                    bits.append(f"Elo gap {ed:+d} to {side}.")
            except (TypeError, ValueError):
                pass
        # Strength differential ONLY when it's meaningful AND we're not
        # just echoing a cold-start seed. games_used > 0 means real data
        # backed the BT input. Below that we say nothing -- the seed is
        # not informative enough to justify a sentence about it.
        sh = inputs.get("strength_home")
        sa = inputs.get("strength_away")
        gh = rc.get("games_used_home", 0)
        ga = rc.get("games_used_away", 0)
        if sh is not None and sa is not None and (gh or 0) >= 5 and (ga or 0) >= 5:
            try:
                diff = float(sh) - float(sa)
                if abs(diff) > 0.08:
                    side = "home" if diff > 0 else "away"
                    bits.append(
                        f"Composer strength favors {side} "
                        f"({float(sh):.2f} vs {float(sa):.2f})."
                    )
            except (TypeError, ValueError):
                pass
        # Pitching matchup -- if upstream attached starter FIPs we can
        # contrast them. Better (lower) FIP gets the nod.
        ph = meta.get("pitching_home") or meta.get("starter_home")
        pa = meta.get("pitching_away") or meta.get("starter_away")
        if isinstance(ph, dict) and isinstance(pa, dict):
            fh = ph.get("fip") or ph.get("era")
            fa = pa.get("fip") or pa.get("era")
            nh = ph.get("name") or "home starter"
            na = pa.get("name") or "away starter"
            if fh is not None and fa is not None:
                try:
                    bits.append(
                        f"Starters: {nh} {float(fh):.2f} vs "
                        f"{na} {float(fa):.2f}."
                    )
                except (TypeError, ValueError):
                    pass

    # ---------------------------------------------------------------
    # Totals: lead with pace + offensive / defensive environment.
    # ---------------------------------------------------------------
    if market_type in ("Total", "Game_Total"):
        pace = inputs.get("pace")
        off = inputs.get("off_env")
        dfn = inputs.get("def_env")
        env_bits = []
        for label, val in (("pace", pace), ("off", off), ("def", dfn)):
            if val is None:
                continue
            try:
                env_bits.append(f"{label}={float(val):.2f}")
            except (TypeError, ValueError):
                pass
        if env_bits:
            bits.append("Run environment " + " ".join(env_bits) + ".")

    # ---------------------------------------------------------------
    # Cross-market context blocks.
    # ---------------------------------------------------------------
    weather = meta.get("weather")
    if isinstance(weather, dict):
        wbits = []
        if weather.get("dome"):
            wbits.append("dome (no weather impact)")
        else:
            wind = weather.get("wind_mph")
            temp = weather.get("temp_f")
            if wind is not None:
                try:
                    direction = weather.get("wind_dir") or ""
                    if direction:
                        wbits.append(f"wind {float(wind):.0f} mph {direction}")
                    else:
                        wbits.append(f"wind {float(wind):.0f} mph")
                except (TypeError, ValueError):
                    pass
            if temp is not None:
                try:
                    wbits.append(f"{float(temp):.0f}°F")
                except (TypeError, ValueError):
                    pass
        if wbits:
            bits.append("Weather: " + ", ".join(wbits) + ".")

    umpire = meta.get("umpire")
    if isinstance(umpire, dict):
        name = umpire.get("name") or "HP umpire"
        kf = umpire.get("k_factor")
        if kf is not None:
            try:
                kf_f = float(kf)
                tilt = "+K" if kf_f > Decimal("1.0") or kf_f > 1.0 else "-K"
                bits.append(f"HP umpire {name} ({tilt} {kf_f:.2f}x).")
            except (TypeError, ValueError):
                bits.append(f"HP umpire {name}.")
        elif name:
            bits.append(f"HP umpire {name}.")

    rest_home = meta.get("rest_days_home")
    rest_away = meta.get("rest_days_away")
    if rest_home is not None and rest_away is not None:
        try:
            rh, ra = float(rest_home), float(rest_away)
            if abs(rh - ra) >= 1.0:
                side = "home" if rh > ra else "away"
                bits.append(f"Rest edge to {side} ({rh:.0f}d vs {ra:.0f}d).")
        except (TypeError, ValueError):
            pass
    travel_miles = meta.get("travel_miles_away")
    if travel_miles is not None:
        try:
            tm = float(travel_miles)
            if tm >= 1500:
                bits.append(f"Away side traveling {tm:.0f} mi.")
        except (TypeError, ValueError):
            pass

    # Barrel-rate / wOBA delta surfacing for HR-style props. Engine
    # consumers can stash these on the market meta and they'll appear
    # verbatim here. No-op when absent.
    barrel = meta.get("barrel_rate")
    if isinstance(barrel, dict):
        delta = barrel.get("delta_pp")
        window = barrel.get("window_days")
        if delta is not None:
            try:
                d_f = float(delta)
                sign = "+" if d_f >= 0 else ""
                w_part = f" ({int(window)}d)" if window is not None else ""
                bits.append(f"Barrel rate {sign}{d_f:.1f}pp{w_part}.")
            except (TypeError, ValueError):
                pass

    # ---------------------------------------------------------------
    # Engine-side audit: HFA, decay, MC band. These are factual and
    # always safe to surface.
    # ---------------------------------------------------------------
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
    if mc_stability:
        try:
            stdev = float(mc_stability.get("stdev", 0))
            p10 = float(mc_stability.get("p10", 0))
            p90 = float(mc_stability.get("p90", 0))
            if stdev > 0 and p90 > p10:
                bits.append(
                    f"MC band {p10:.2f}-{p90:.2f} (sigma {stdev:.3f})."
                )
        except (TypeError, ValueError):
            pass

    # Sample-size caveat -- dropped at the end so subscribers know when
    # the projection is leaning on the league prior. Never apologetic;
    # it's a credibility statement.
    if rc.get("sample_warning"):
        bits.append(
            "Limited settled-game history; projection blended toward "
            "the league prior."
        )

    # NO generic price/probability fallback. If we can't say anything
    # specific, the Read field stays empty and the renderer falls back
    # to its own "engine flagged on price/probability delta" line --
    # we no longer pre-fill that here so the absence is visible to
    # the auditor instead of looking like a real analytical read.
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
        # Phase 30: any caller-supplied mc_stability (from a higher-level
        # orchestrator that's already run MC) wins. Otherwise the engine
        # runs its own 10k MC in the PROB_MARKETS branch below. Stays
        # None on EXPECTATION_MARKETS (no fair_prob -> nothing to sample).
        grade = "C"
        realization = 47
        sanity_reason: Optional[str] = None
        confidence_capped_reason: Optional[str] = None

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

            # ----------------------------------------------------------
            # Phase 30: 10k Monte Carlo around the fair-prob estimate.
            # For ML we perturb the Bradley-Terry strengths (the true
            # source of input uncertainty); for BTTS we fall back to
            # a logit-space perturbation of the point estimate. The
            # result (stdev / p10 / p90) is both stashed in metadata
            # for audit and handed to the MVS detector later in this
            # method. Deterministic: same game_id + inputs produce the
            # same distribution across runs.
            # ----------------------------------------------------------
            if fair_prob is not None:
                try:
                    if market == "ML":
                        mc_result = MonteCarloSimulator.simulate_ml(
                            strength_home=float(bundle.inputs.get("strength_home", 1.0)),
                            strength_away=float(bundle.inputs.get("strength_away", 1.0)),
                            home_adv=float(bundle.inputs.get("home_adv", 0.0)),
                            seed_key=f"{bundle.game_id or ''}:{selection}",
                        )
                    else:
                        mc_result = MonteCarloSimulator.simulate_point_prob(
                            fair_prob=fair_prob,
                            seed_key=f"{bundle.game_id or ''}:{market}:{selection}",
                        )
                    mc_stability = mc_result.to_dict()
                except (TypeError, ValueError):
                    mc_stability = None

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

            # Confidence penalty: strength-driven markets need real
            # settled-game samples to be trustworthy. Cap grade at C
            # below the threshold and zero out Kelly so a thin-data
            # pick can never size up. Audit trail flows through
            # metadata so an operator can tell which picks were
            # demoted vs which were graded purely on edge.
            if market in _STRENGTH_DRIVEN_MARKETS:
                rc = (bundle.metadata or {}).get("read_context") or {}
                gh = rc.get("games_used_home", 0) or 0
                ga = rc.get("games_used_away", 0) or 0
                min_used = min(gh, ga)
                if min_used < _MIN_CONFIDENT_GAMES_USED:
                    confidence_capped_reason = (
                        f"games_used min={min_used} below "
                        f"threshold={_MIN_CONFIDENT_GAMES_USED} "
                        f"(home={gh}, away={ga})"
                    )
                    if grade not in ("C", "D", "F"):
                        grade = "C"
                        realization = ConfidenceScorer.realization_for_grade(grade)
                        kelly = Decimal("0")

        elif market in EXPECTATION_MARKETS:
            if "expected_total" in fv:
                expected_value = fv["expected_total"]
            elif "expected_value" in fv:
                expected_value = fv["expected_value"]
            edge = None
            kelly = None

        elif market in FIRST_INNING_MARKETS:
            # NRFI / YRFI: fair_prob comes from the first-inning Poisson
            # helper in ProbabilityCalculator. Selection maps Yes->home,
            # No->away the same way BTTS does.
            fair_prob = fv.get("fair_prob")
            if fair_prob is not None:
                sel_lower = selection.strip().lower() if selection else ""
                if sel_lower in ("no", "nrfi"):
                    # "No runs" side matches NRFI's home-centric fair_prob
                    # directly; for a YRFI market we mirror.
                    if market == "YRFI":
                        fair_prob = (Decimal("1") - fair_prob).quantize(
                            Decimal("0.000001")
                        )
                elif sel_lower in ("yes", "yrfi"):
                    if market == "NRFI":
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
                mc_stability=mc_stability,
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
        if confidence_capped_reason:
            meta["confidence_capped_reason"] = confidence_capped_reason
        # Phase 30: stash MC stability into metadata so auditors (and
        # the MVS detector on the far side of this call) can see the
        # exact distribution numbers that gated this pick.
        if mc_stability:
            meta["mc_stability"] = dict(mc_stability)

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
