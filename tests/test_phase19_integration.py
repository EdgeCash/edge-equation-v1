"""
Phase 19 end-to-end: Bradley-Terry team-strength builder feeds the engine.

Before Phase 19, FeatureComposer.compose mapped Elo straight to BT strength
(exp((rating - 1500) / 400)). Phase 19 replaces that with the full
TeamStrengthBuilder blend (Pythagorean + decay-weighted form + Elo +
optional pitching). These tests exercise the full flow:

    GameResultsStore -> FeatureComposer.compose (with TeamStrengthBuilder)
                      -> engine.bradley_terry -> Pick.fair_prob

All deterministic. No ML, no RNG.
"""
from decimal import Decimal

import pytest

from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.pick_schema import Line
from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.stats.composer import FeatureComposer
from edge_equation.stats.elo import EloCalculator
from edge_equation.stats.results import GameResult
from edge_equation.stats.team_strength import PitchingInputs, TeamStrengthBuilder


def _g(gid, home, away, hs, as_, start="2026-04-13T18:30:00+09:00", league="MLB"):
    return GameResult(
        result_id=None, game_id=gid, league=league,
        home_team=home, away_team=away, start_time=start,
        home_score=hs, away_score=as_, status="final",
    )


def _build_strong_weak_history(league="MLB", n=20):
    """Team A wins 20 straight against team B, 6-2."""
    return [_g(f"G{i}", "A", "B", 6, 2, league=league) for i in range(n)]


# ------------------------------------------------ compose -> engine


def test_compose_produces_higher_home_prob_for_strong_home():
    games = _build_strong_weak_history()
    features = FeatureComposer.compose("A", "B", "MLB", games)
    # Plug composed strengths into Bradley-Terry directly.
    prob = ProbabilityCalculator.bradley_terry(
        features.ml_inputs["strength_home"],
        features.ml_inputs["strength_away"],
        features.ml_inputs["home_adv"],
    )
    # Tango shrinkage at form_window=15 caps a dominant home team in
    # the high-50s to mid-60s range against a weak opponent -- which is
    # what real MLB closing lines say. Pre-shrinkage this assertion was
    # > 0.70, but that was the over-confidence we're now correcting.
    assert prob > Decimal('0.60'), (
        f"strong home should still beat the 60% threshold; got {prob}"
    )
    assert prob < Decimal('0.75'), (
        f"strong home should NOT exceed 75% (that was the pre-shrinkage "
        f"over-confidence pathology); got {prob}"
    )


def test_compose_reverses_when_weak_team_is_home():
    games = _build_strong_weak_history()
    # B is home, A is away. B has been losing 2-6; home win prob should be low.
    features = FeatureComposer.compose("B", "A", "MLB", games)
    prob = ProbabilityCalculator.bradley_terry(
        features.ml_inputs["strength_home"],
        features.ml_inputs["strength_away"],
        features.ml_inputs["home_adv"],
    )
    # Mirror image of the strong-home test: weak home should land in
    # the mid-30s to low-40s, not below 30% (that was pre-shrinkage).
    assert prob > Decimal('0.25'), prob
    assert prob < Decimal('0.45'), (
        f"weak home should NOT drop below 45% under shrinkage; got {prob}. "
        f"The pre-shrinkage <30% was the same over-confidence in reverse."
    )


def test_compose_uses_sport_home_adv():
    games = _build_strong_weak_history(league="NFL")
    features = FeatureComposer.compose("A", "B", "NFL", games)
    # NFL home_adv is 0.150 per SPORT_CONFIG, not the MLB default 0.115.
    assert abs(features.ml_inputs["home_adv"] - 0.150) < 1e-6


def test_compose_empty_history_yields_near_neutral_strengths_and_toss_up():
    """Phase 31: cold start seeds strengths with a small deterministic
    perturbation (sha256-derived) instead of a flat 1.0. The BT
    projection stays close to the home-adv-only toss-up, just nudged
    by the seed so two teams never collapse to identical inputs."""
    features = FeatureComposer.compose("A", "B", "MLB", [])
    assert abs(features.ml_inputs["strength_home"] - 1.0) < 0.035
    assert abs(features.ml_inputs["strength_away"] - 1.0) < 0.035
    prob = ProbabilityCalculator.bradley_terry(
        features.ml_inputs["strength_home"],
        features.ml_inputs["strength_away"],
        features.ml_inputs["home_adv"],
    )
    # With home_adv=0.115 and near-equal strengths the projection stays
    # inside the home-toss-up band; the seed never pushes it out.
    assert Decimal('0.47') < prob < Decimal('0.58')


def test_full_slate_runner_flow_end_to_end():
    # End-to-end sanity check: composer -> bundle -> engine -> Pick.
    # We only need a moderate favorite signal here -- a runaway 20-0
    # history pushes fair_prob to the 0.99 clamp and trips the Phase
    # 28 +30%-edge sanity guard, which is the right behavior for
    # nonsense data but not what this test is exercising.
    games = _build_strong_weak_history(n=8)
    features = FeatureComposer.compose("A", "B", "MLB", games)
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs=features.ml_inputs,
        universal_features={},
        selection="A",
        game_id="MLB-2026-04-20-B-A",
        metadata={"home_team": "A", "away_team": "B"},
    )
    # Use a more realistic favorite line so the implied probability
    # leaves room for a positive edge under the 30% sanity ceiling.
    pick = BettingEngine.evaluate(bundle, Line(odds=-130))
    # Heavy favorite -> fair_prob in upper range; edge present and
    # positive (well below the 30% sanity guard).
    assert pick.fair_prob is not None
    assert pick.fair_prob > Decimal("0.5")
    if pick.edge is not None:
        assert pick.edge > Decimal("0")


# ------------------------------------------------ baseball pitching


def test_pitching_inputs_affect_strength_for_mlb():
    # Mid-range record (10-5) so the team's blended strength doesn't
    # already saturate at the ceiling -- with a perfect 15-0 record
    # both great-pitching and awful-pitching variants would clamp to
    # STRENGTH_CEIL and the pitching differential would be invisible.
    games = [_g(f"W{i}", "A", "B", 5, 3) for i in range(10)]
    games += [_g(f"L{i}", "A", "B", 2, 4) for i in range(5)]
    elo = EloCalculator.replay("MLB", games)

    # Team A with elite pitching
    great = PitchingInputs(
        starter_fip=Decimal('2.60'),
        bullpen_fip=Decimal('2.80'),
        league_fip=Decimal('4.20'),
    )
    # Team A with awful pitching
    awful = PitchingInputs(
        starter_fip=Decimal('5.80'),
        bullpen_fip=Decimal('5.50'),
        league_fip=Decimal('4.20'),
    )

    ts_great = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo, pitching=great,
    )
    ts_awful = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo, pitching=awful,
    )
    assert ts_great.strength > ts_awful.strength


def test_pitching_passed_through_compose():
    # Use a near-average record so the pitching multiplier actually moves
    # the blended strength rather than being diluted by a saturated form
    # component.
    games = []
    for i in range(15):
        if i % 2 == 0:
            games.append(_g(f"W{i}", "A", "B", 5, 3))
        else:
            games.append(_g(f"L{i}", "A", "B", 3, 5))
    great = PitchingInputs(
        starter_fip=Decimal('2.60'),
        bullpen_fip=Decimal('2.80'),
        league_fip=Decimal('4.20'),
    )
    f_with_pitch = FeatureComposer.compose(
        "A", "B", "MLB", games, home_pitching=great,
    )
    f_without = FeatureComposer.compose("A", "B", "MLB", games)
    assert f_with_pitch.ml_inputs["strength_home"] > f_without.ml_inputs["strength_home"]


# ------------------------------------------------ KBO / NPB parity


def test_kbo_uses_same_pythagorean_convention_as_mlb():
    # Same-shape series in KBO should produce a comparable strength range.
    kbo = [_g(f"G{i}", "A", "B", 6, 2, league="KBO") for i in range(15)]
    mlb = [_g(f"G{i}", "A", "B", 6, 2, league="MLB") for i in range(15)]
    ts_kbo = TeamStrengthBuilder.build(
        team="A", league="KBO", results=kbo, elo=EloCalculator.replay("KBO", kbo),
    )
    ts_mlb = TeamStrengthBuilder.build(
        team="A", league="MLB", results=mlb, elo=EloCalculator.replay("MLB", mlb),
    )
    # Within the same regime (exponent, weights, decay) -> identical strengths.
    assert ts_kbo.strength == ts_mlb.strength


# ------------------------------------------------ determinism


def test_full_flow_is_deterministic():
    games = _build_strong_weak_history()
    f1 = FeatureComposer.compose("A", "B", "MLB", games)
    f2 = FeatureComposer.compose("A", "B", "MLB", games)
    assert f1.ml_inputs == f2.ml_inputs
    assert f1.totals_inputs == f2.totals_inputs
