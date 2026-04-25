import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Pick, Line
from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.scoring import ConfidenceScorer


def _make_ml_bundle_det_at_bos():
    # Phase 28: BettingEngine now requires home_team/away_team in
    # metadata so it can flip the home-centric fair_prob when grading
    # the away selection. The test selection "BOS" is the home team
    # here (game_id "...-DET-BOS" reads as DET-at-BOS), so fair_prob
    # should pass through unchanged.
    return FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
        metadata={
            "home_team": "BOS", "away_team": "DET",
            # Enough settled-game evidence so the confidence penalty (cap
            # at C below threshold) doesn't kick in -- this test exercises
            # the math layer, not the sample-size guardrail.
            "read_context": {"games_used_home": 50, "games_used_away": 50},
        },
    )


def test_engine_ml_pick_matches_math_layer():
    bundle = _make_ml_bundle_det_at_bos()
    line = Line(odds=-132)
    pick = BettingEngine.evaluate(bundle, line, public_mode=False)

    fv = ProbabilityCalculator.calculate_fair_value(
        "ML", "MLB", bundle.inputs, bundle.universal_features
    )
    expected_fair_prob = fv["fair_prob"]
    expected_edge = EVCalculator.calculate_edge(expected_fair_prob, -132)
    dec_odds = EVCalculator.american_to_decimal(-132)
    expected_kelly_full = EVCalculator.kelly_fraction(expected_edge, dec_odds)
    expected_kelly_half = (expected_kelly_full / Decimal('2')).quantize(Decimal('0.0001'))
    expected_grade = ConfidenceScorer.grade(expected_edge)

    assert isinstance(pick, Pick)
    assert pick.fair_prob == expected_fair_prob
    assert pick.expected_value is None
    assert pick.edge == expected_edge
    if expected_edge >= Decimal('0.010000'):
        assert pick.kelly == expected_kelly_half
    else:
        assert pick.kelly == Decimal('0')
    assert pick.grade == expected_grade
    assert pick.realization == ConfidenceScorer.realization_for_grade(expected_grade)
    assert pick.sport == "MLB"
    assert pick.market_type == "ML"
    assert pick.selection == "BOS"
    assert pick.line.odds == -132
    assert pick.game_id == "MLB-2026-04-20-DET-BOS"


def test_engine_total_pick_returns_expected_value():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="Total",
        inputs={"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        universal_features={},
        selection="Over 9.5",
    )
    line = Line(odds=-110, number=Decimal('9.5'))
    pick = BettingEngine.evaluate(bundle, line)

    fv = ProbabilityCalculator.calculate_fair_value(
        "Total", "MLB", bundle.inputs, bundle.universal_features
    )
    assert pick.expected_value == fv["expected_total"]
    assert pick.fair_prob is None
    assert pick.edge is None
    assert pick.kelly is None


def test_engine_hr_prop_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="HR",
        inputs={"rate": 0.142},
        universal_features={"matchup_exploit": 0.08, "market_line_delta": 0.12},
        selection="Judge Over 0.5 HR",
    )
    line = Line(odds=+320, number=Decimal('0.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("HR", "MLB", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]
    assert pick.fair_prob is None


def test_engine_k_prop_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="K",
        inputs={"rate": 7.85},
        universal_features={"matchup_exploit": 0.09, "market_line_delta": 0.08},
        selection="Burnes Over 7.5 K",
    )
    line = Line(odds=-115, number=Decimal('7.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("K", "MLB", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_nfl_passing_yards_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="NFL",
        market_type="Passing_Yards",
        inputs={"rate": 312.4},
        universal_features={"form_off": 0.11, "matchup_strength": 0.09},
        selection="Mahomes Over 275.5",
    )
    line = Line(odds=-110, number=Decimal('275.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("Passing_Yards", "NFL", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_nhl_sog_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="NHL",
        market_type="SOG",
        inputs={"rate": 4.12},
        universal_features={"matchup_exploit": 0.10},
        selection="Crosby Over 4.5 SOG",
    )
    line = Line(odds=-115, number=Decimal('4.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("SOG", "NHL", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_public_mode_suppresses_edge_kelly():
    bundle = _make_ml_bundle_det_at_bos()
    line = Line(odds=-132)
    pick = BettingEngine.evaluate(bundle, line, public_mode=True)
    assert pick.fair_prob is not None
    assert pick.edge is None
    assert pick.kelly is None


def test_pick_is_frozen():
    bundle = _make_ml_bundle_det_at_bos()
    pick = BettingEngine.evaluate(bundle, Line(odds=-132))
    with pytest.raises(Exception):
        pick.edge = Decimal('0.5')


# ---------------------------------------------------------------------------
# Confidence penalty: strength-driven markets need a meaningful sample
# of settled games on each team, otherwise the engine's edge claim is
# a cold-start artifact and the grade gets capped at C.
# ---------------------------------------------------------------------------


def _make_ml_bundle_with_games_used(home_used: int, away_used: int):
    return FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
        metadata={
            "home_team": "BOS", "away_team": "DET",
            "read_context": {
                "games_used_home": home_used,
                "games_used_away": away_used,
            },
        },
    )


def test_engine_confidence_penalty_caps_grade_when_sample_is_thin():
    """Below the games_used threshold on either side, the grade is
    capped at C and Kelly is zeroed. Audit trail flows through
    metadata['confidence_capped_reason'] so an operator can see which
    picks were demoted vs which were graded purely on edge."""
    bundle = _make_ml_bundle_with_games_used(home_used=3, away_used=4)
    pick = BettingEngine.evaluate(bundle, Line(odds=-132), public_mode=False)
    assert pick.grade == "C"
    assert pick.kelly == Decimal("0")
    # Edge / fair_prob still come from the math layer -- the penalty
    # only adjusts grade + Kelly, not the underlying numbers, so the
    # operator can audit how big the engine *thought* the edge was.
    assert pick.edge is not None
    assert pick.fair_prob is not None
    reason = (pick.metadata or {}).get("confidence_capped_reason") or ""
    assert "games_used" in reason and "threshold" in reason


def test_engine_confidence_penalty_no_op_when_sample_meets_threshold():
    """At >= the threshold on both sides, the penalty doesn't fire and
    the pick grades on its computed edge as before."""
    bundle = _make_ml_bundle_with_games_used(home_used=50, away_used=50)
    pick = BettingEngine.evaluate(bundle, Line(odds=-132), public_mode=False)
    # No confidence_capped_reason recorded.
    assert "confidence_capped_reason" not in (pick.metadata or {})
    # Grade is whatever ConfidenceScorer.grade(edge) returns -- not
    # forced to C by the penalty.
    expected_grade = ConfidenceScorer.grade(pick.edge)
    assert pick.grade == expected_grade


def test_engine_confidence_penalty_uses_min_of_two_sides():
    """Both teams have to clear the threshold. One thin side is enough
    to trigger the penalty -- the engine has no way to project well
    when half the matchup is poorly observed."""
    bundle = _make_ml_bundle_with_games_used(home_used=50, away_used=2)
    pick = BettingEngine.evaluate(bundle, Line(odds=-132), public_mode=False)
    assert pick.grade == "C"
    assert pick.kelly == Decimal("0")


def test_engine_confidence_penalty_does_not_promote_F_or_D_to_C():
    """If the math already grades the pick worse than C (e.g. F for a
    sharply negative edge), the penalty must NOT silently promote it
    upward to C. The penalty caps the upper bound, it doesn't set a
    floor."""
    # Use odds that produce a strongly negative edge so ConfidenceScorer
    # returns F. Combine with thin sample so the penalty would otherwise
    # cap at C.
    bundle = _make_ml_bundle_with_games_used(home_used=2, away_used=2)
    # -1000 is a wildly overpriced favorite -> negative edge -> F grade.
    pick = BettingEngine.evaluate(bundle, Line(odds=-1000), public_mode=False)
    # F should remain F. The penalty's "if grade not in (C, D, F)" guard
    # is what prevents accidental promotion.
    assert pick.grade in ("D", "F"), (
        f"penalty should not promote a sub-C math grade upward; got {pick.grade}"
    )


def test_engine_confidence_penalty_does_not_apply_to_non_strength_markets():
    """Total / props / NRFI / YRFI / BTTS don't depend on Bradley-Terry
    team strengths, so the games_used threshold doesn't apply to them.
    A Total pick with a thin sample is still graded on its math."""
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="Total",
        inputs={
            "off_env": 1.18, "def_env": 1.07, "pace": 1.03,
            "dixon_coles_adj": 0.00,
        },
        universal_features={},
        selection="Over 9.5",
        metadata={
            "home_team": "BOS", "away_team": "DET",
            # Thin games_used would trigger the penalty IF this market
            # were strength-driven. It isn't.
            "read_context": {"games_used_home": 1, "games_used_away": 1},
        },
    )
    pick = BettingEngine.evaluate(
        bundle, Line(odds=-110, number=Decimal("9.5")), public_mode=False,
    )
    assert "confidence_capped_reason" not in (pick.metadata or {})
    assert pick.expected_value is not None


def test_engine_confidence_penalty_threshold_is_reachable():
    """Regression guard for the bug that produced empty Premium Daily
    emails: the threshold had been set to 20 but FeatureComposer's
    games_used counters are capped at form_window_games (15 for MLB,
    10 for NHL/NBA, 5 for NFL). A threshold above the form-window cap
    is unreachable for the relevant sports, so every strength-driven
    pick gets capped at C indefinitely.

    This test pins the threshold at 10 -- low enough that NHL and NBA
    teams with a full 10-game form window can clear it, low enough
    that MLB teams with a 10+ game window do too. Bumping back above
    10 silently re-introduces the empty-output regression."""
    from edge_equation.engine.betting_engine import _MIN_CONFIDENT_GAMES_USED
    assert _MIN_CONFIDENT_GAMES_USED <= 10, (
        f"_MIN_CONFIDENT_GAMES_USED={_MIN_CONFIDENT_GAMES_USED} exceeds "
        "form_window_games for NHL/NBA (10) and would silently make "
        "every strength-driven pick C-capped, even with comprehensive "
        "season data. See the docstring on the constant for context."
    )
    # Also verify the boundary: games_used == threshold should NOT
    # trigger the cap (the check is `< threshold`, not `<=`).
    bundle = _make_ml_bundle_with_games_used(
        home_used=_MIN_CONFIDENT_GAMES_USED,
        away_used=_MIN_CONFIDENT_GAMES_USED,
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-132), public_mode=False)
    assert "confidence_capped_reason" not in (pick.metadata or {}), (
        "games_used == threshold should clear the cap"
    )
    # And one below the threshold DOES trigger.
    bundle = _make_ml_bundle_with_games_used(
        home_used=_MIN_CONFIDENT_GAMES_USED - 1,
        away_used=_MIN_CONFIDENT_GAMES_USED - 1,
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-132), public_mode=False)
    assert "confidence_capped_reason" in (pick.metadata or {}), (
        "games_used == threshold-1 must trigger the cap"
    )
