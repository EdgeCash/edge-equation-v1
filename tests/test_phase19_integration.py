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
    assert prob > Decimal('0.70')


def test_compose_reverses_when_weak_team_is_home():
    games = _build_strong_weak_history()
    # B is home, A is away. B has been losing 2-6; home win prob should be low.
    features = FeatureComposer.compose("B", "A", "MLB", games)
    prob = ProbabilityCalculator.bradley_terry(
        features.ml_inputs["strength_home"],
        features.ml_inputs["strength_away"],
        features.ml_inputs["home_adv"],
    )
    assert prob < Decimal('0.30')


def test_compose_uses_sport_home_adv():
    games = _build_strong_weak_history(league="NFL")
    features = FeatureComposer.compose("A", "B", "NFL", games)
    # NFL home_adv is 0.150 per SPORT_CONFIG, not the MLB default 0.115.
    assert abs(features.ml_inputs["home_adv"] - 0.150) < 1e-6


def test_compose_empty_history_yields_neutral_strengths_and_toss_up():
    features = FeatureComposer.compose("A", "B", "MLB", [])
    # Both strengths neutral (1.0) -> BT returns home_adv-only edge.
    assert abs(features.ml_inputs["strength_home"] - 1.0) < 1e-6
    assert abs(features.ml_inputs["strength_away"] - 1.0) < 1e-6
    prob = ProbabilityCalculator.bradley_terry(
        features.ml_inputs["strength_home"],
        features.ml_inputs["strength_away"],
        features.ml_inputs["home_adv"],
    )
    # With home_adv=0.115 and equal strengths, home win prob is e^0.115 /
    # (e^0.115 + 1) ~= 0.529. Assert in a reasonable band.
    assert Decimal('0.51') < prob < Decimal('0.55')


def test_full_slate_runner_flow_end_to_end():
    games = _build_strong_weak_history(n=25)
    features = FeatureComposer.compose("A", "B", "MLB", games)
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs=features.ml_inputs,
        universal_features={},
        selection="A",
        game_id="MLB-2026-04-20-B-A",
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-180))
    # Heavy favorite -> fair_prob > implied_prob(0.643) and edge positive
    assert pick.fair_prob is not None
    assert pick.edge is not None
    assert pick.edge > Decimal('0')


# ------------------------------------------------ baseball pitching


def test_pitching_inputs_affect_strength_for_mlb():
    games = [_g(f"G{i}", "A", "B", 5, 4) for i in range(15)]
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
