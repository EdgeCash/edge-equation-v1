from decimal import Decimal
import math
import pytest

from edge_equation.stats.elo import EloCalculator, EloRatings, STARTING_RATING
from edge_equation.stats.results import GameResult
from edge_equation.stats.team_strength import (
    NEUTRAL_STRENGTH,
    STRENGTH_CEIL,
    STRENGTH_FLOOR,
    PitchingInputs,
    TeamStrength,
    TeamStrengthBuilder,
    TeamStrengthComponents,
)


# -------------------------------------------------- helpers


def _g(game_id, home, away, hs, as_, start="2026-04-13T18:30:00+09:00", league="MLB"):
    return GameResult(
        result_id=None, game_id=game_id, league=league,
        home_team=home, away_team=away, start_time=start,
        home_score=hs, away_score=as_, status="final",
    )


# -------------------------------------------------- pythagorean_strength


def test_pythagorean_even_scoring_yields_one():
    s = TeamStrengthBuilder.pythagorean_strength(
        rs=Decimal('50'), ra=Decimal('50'), exponent=Decimal('1.83'),
    )
    assert s == Decimal('1.000000') or abs(s - Decimal('1.000000')) < Decimal('0.000002')


def test_pythagorean_higher_scored_greater_than_one():
    s = TeamStrengthBuilder.pythagorean_strength(
        rs=Decimal('100'), ra=Decimal('50'), exponent=Decimal('1.83'),
    )
    assert s > Decimal('1')


def test_pythagorean_lower_scored_less_than_one():
    s = TeamStrengthBuilder.pythagorean_strength(
        rs=Decimal('30'), ra=Decimal('80'), exponent=Decimal('1.83'),
    )
    assert s < Decimal('1')


def test_pythagorean_zero_games_returns_none():
    s = TeamStrengthBuilder.pythagorean_strength(
        rs=Decimal('0'), ra=Decimal('0'), exponent=Decimal('1.83'),
    )
    assert s is None


def test_pythagorean_negative_inputs_rejected():
    with pytest.raises(ValueError):
        TeamStrengthBuilder.pythagorean_strength(
            rs=Decimal('-1'), ra=Decimal('5'), exponent=Decimal('1.83'),
        )


def test_pythagorean_steeper_exponent_amplifies_differential():
    # Same RS/RA but higher exponent -> stronger signal -> higher strength
    low = TeamStrengthBuilder.pythagorean_strength(
        rs=Decimal('60'), ra=Decimal('50'), exponent=Decimal('1.83'),
    )
    high = TeamStrengthBuilder.pythagorean_strength(
        rs=Decimal('60'), ra=Decimal('50'), exponent=Decimal('11.5'),
    )
    assert high > low


# -------------------------------------------------- form_strength


def test_form_empty_returns_none():
    s, n = TeamStrengthBuilder.form_strength(
        games=[], team="A", decay_lambda=Decimal('0.95'), window=15,
    )
    assert s is None
    assert n == 0


def test_form_all_wins_strength_high():
    games = [_g(f"G{i}", "A", "B", 6, 2) for i in range(10)]
    s, n = TeamStrengthBuilder.form_strength(
        games=games, team="A", decay_lambda=Decimal('0.95'), window=15,
    )
    assert n == 10
    assert s > Decimal('5')  # heavily winning team -> very strong


def test_form_all_losses_strength_low():
    games = [_g(f"G{i}", "A", "B", 2, 6) for i in range(10)]
    s, _ = TeamStrengthBuilder.form_strength(
        games=games, team="A", decay_lambda=Decimal('0.95'), window=15,
    )
    assert s < Decimal('0.25')


def test_form_decay_lambda_weights_recent_games_more():
    # A lost the last 3 but won the earlier 5. With a heavy decay, the
    # recent losses dominate -> strength < 1.
    games: list = []
    base_date = 20
    for i in range(5):
        games.append(_g(f"W{i}", "A", "B", 8, 2, start=f"2026-04-0{i+1}T00:00:00"))
    for i in range(3):
        games.append(_g(f"L{i}", "A", "B", 1, 9, start=f"2026-04-1{i}T00:00:00"))

    s_heavy, _ = TeamStrengthBuilder.form_strength(
        games=games, team="A", decay_lambda=Decimal('0.5'), window=15,
    )
    s_mild, _ = TeamStrengthBuilder.form_strength(
        games=games, team="A", decay_lambda=Decimal('0.99'), window=15,
    )
    # Heavy decay (0.5) = recent-only -> mostly losses -> weaker
    # Mild decay (0.99) = near-uniform -> 5W 3L -> stronger
    assert s_heavy < s_mild


def test_form_draw_counts_half():
    games = [
        _g(f"W{i}", "A", "B", 3, 1) for i in range(2)
    ] + [_g("D", "A", "B", 2, 2)]
    s, _ = TeamStrengthBuilder.form_strength(
        games=games, team="A", decay_lambda=Decimal('0.95'), window=15,
    )
    # 2.5 / 3 = 0.833... -> strength ~5
    assert s > Decimal('3')


def test_form_invalid_lambda_rejected():
    games = [_g("G", "A", "B", 5, 3)]
    with pytest.raises(ValueError):
        TeamStrengthBuilder.form_strength(
            games=games, team="A", decay_lambda=Decimal('0'), window=10,
        )
    with pytest.raises(ValueError):
        TeamStrengthBuilder.form_strength(
            games=games, team="A", decay_lambda=Decimal('1.5'), window=10,
        )


def test_form_ignores_games_team_not_in():
    games = [
        _g("G1", "A", "B", 8, 2),
        _g("G2", "C", "D", 0, 100),   # A not in this one
    ]
    s, n = TeamStrengthBuilder.form_strength(
        games=games, team="A", decay_lambda=Decimal('0.95'), window=10,
    )
    assert n == 1  # only G1 counts


# -------------------------------------------------- elo_strength


def test_elo_strength_none_when_elo_missing():
    s = TeamStrengthBuilder.elo_strength(team="A", elo=None)
    assert s is None


def test_elo_strength_none_when_team_not_in_ratings():
    elo = EloRatings(league="MLB", ratings={"A": Decimal('1600')}, games_seen={"A": 10})
    s = TeamStrengthBuilder.elo_strength(team="B", elo=elo)
    assert s is None


def test_elo_strength_starting_rating_is_one():
    elo = EloRatings(league="MLB", ratings={"A": STARTING_RATING}, games_seen={"A": 1})
    s = TeamStrengthBuilder.elo_strength(team="A", elo=elo)
    assert abs(s - Decimal('1')) < Decimal('0.00001')


def test_elo_strength_matches_expected_formula():
    elo = EloRatings(league="MLB", ratings={"A": Decimal('1700')}, games_seen={"A": 5})
    s = TeamStrengthBuilder.elo_strength(team="A", elo=elo)
    expected = math.exp((1700 - 1500) / 400.0)  # ~1.6487
    assert abs(float(s) - expected) < 1e-5


# -------------------------------------------------- pitching_strength


def test_pitching_none_when_inputs_missing():
    assert TeamStrengthBuilder.pitching_strength(None, Decimal('0.2')) is None
    inputs = PitchingInputs(
        starter_fip=Decimal('3.50'),
        bullpen_fip=Decimal('3.80'),
        league_fip=Decimal('4.20'),
    )
    assert TeamStrengthBuilder.pitching_strength(inputs, None) is None


def test_pitching_equal_to_league_yields_one():
    inputs = PitchingInputs(
        starter_fip=Decimal('4.20'),
        bullpen_fip=Decimal('4.20'),
        league_fip=Decimal('4.20'),
    )
    s = TeamStrengthBuilder.pitching_strength(inputs, Decimal('0.2'))
    assert abs(s - Decimal('1')) < Decimal('0.00001')


def test_pitching_better_than_league_yields_greater_than_one():
    inputs = PitchingInputs(
        starter_fip=Decimal('3.00'),
        bullpen_fip=Decimal('3.20'),
        league_fip=Decimal('4.20'),
    )
    s = TeamStrengthBuilder.pitching_strength(inputs, Decimal('0.2'))
    assert s > Decimal('1.2')


def test_pitching_worse_than_league_yields_less_than_one():
    inputs = PitchingInputs(
        starter_fip=Decimal('5.50'),
        bullpen_fip=Decimal('5.20'),
        league_fip=Decimal('4.20'),
    )
    s = TeamStrengthBuilder.pitching_strength(inputs, Decimal('0.2'))
    assert s < Decimal('1')


def test_pitching_weight_must_be_valid():
    inputs = PitchingInputs(
        starter_fip=Decimal('3.5'),
        bullpen_fip=Decimal('3.8'),
        league_fip=Decimal('4.2'),
    )
    with pytest.raises(ValueError):
        TeamStrengthBuilder.pitching_strength(inputs, Decimal('-0.1'))
    with pytest.raises(ValueError):
        TeamStrengthBuilder.pitching_strength(inputs, Decimal('1.1'))


def test_pitching_zero_league_fip_raises():
    inputs = PitchingInputs(
        starter_fip=Decimal('3.5'),
        bullpen_fip=Decimal('3.8'),
        league_fip=Decimal('0'),
    )
    with pytest.raises(ValueError):
        TeamStrengthBuilder.pitching_strength(inputs, Decimal('0.2'))


# -------------------------------------------------- build() end-to-end


def test_build_no_data_returns_neutral():
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=[],
    )
    assert ts.strength == NEUTRAL_STRENGTH
    assert ts.games_used == 0


def test_build_strong_team_returns_strength_above_one():
    games = [_g(f"G{i}", "A", "B", 8, 2) for i in range(20)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    assert ts.strength > Decimal('1.5')
    assert ts.games_used > 0


def test_build_weak_team_returns_strength_below_one():
    games = [_g(f"G{i}", "A", "B", 1, 9) for i in range(20)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    assert ts.strength < Decimal('1')


def test_build_returns_audit_components():
    games = [_g(f"G{i}", "A", "B", 6, 3) for i in range(15)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    assert isinstance(ts, TeamStrength)
    assert isinstance(ts.components, TeamStrengthComponents)
    # Every source except pitching should be populated.
    assert ts.components.pyth is not None
    assert ts.components.form is not None
    assert ts.components.elo is not None
    assert ts.components.pitching is None
    # Effective weights renormalize to sum to 1.
    total = sum(ts.effective_weights.values())
    assert abs(total - Decimal('1')) < Decimal('0.00001')


def test_build_pitching_only_available_for_baseball():
    # NFL strength_blend has pitching=0.0 so pitching inputs are a no-op
    games = [_g(f"G{i}", "A", "B", 28, 14, league="NFL") for i in range(4)]
    pitch = PitchingInputs(
        starter_fip=Decimal('2.50'),
        bullpen_fip=Decimal('2.80'),
        league_fip=Decimal('4.20'),
    )
    ts = TeamStrengthBuilder.build(
        team="A", league="NFL", results=games, pitching=pitch,
        bullpen_weight=Decimal('0.2'),
    )
    # pitching weight 0 in NFL blend -> pitching component may compute
    # but must NOT appear in effective_weights.
    assert "pitching" not in ts.effective_weights


def test_build_pitching_contributes_for_mlb():
    games = [_g(f"G{i}", "A", "B", 5, 4) for i in range(20)]
    elo = EloCalculator.replay("MLB", games)
    pitch = PitchingInputs(
        starter_fip=Decimal('2.80'),
        bullpen_fip=Decimal('3.00'),
        league_fip=Decimal('4.20'),
    )
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo, pitching=pitch,
    )
    assert ts.components.pitching is not None
    assert "pitching" in ts.effective_weights


def test_build_strength_clamped_to_ceiling():
    # Comically lopsided scoring should still clamp to STRENGTH_CEIL.
    games = [_g(f"G{i}", "A", "B", 20, 0) for i in range(30)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    assert ts.strength <= STRENGTH_CEIL


def test_build_strength_clamped_to_floor():
    games = [_g(f"G{i}", "A", "B", 0, 20) for i in range(30)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    assert ts.strength >= STRENGTH_FLOOR


def test_build_deterministic():
    games = [_g(f"G{i}", "A", "B", 7, 3) for i in range(12)]
    elo = EloCalculator.replay("MLB", games)
    ts1 = TeamStrengthBuilder.build(team="A", league="MLB", results=games, elo=elo)
    ts2 = TeamStrengthBuilder.build(team="A", league="MLB", results=games, elo=elo)
    assert ts1.strength == ts2.strength
    assert ts1.effective_weights == ts2.effective_weights


def test_build_filters_by_league():
    games = [
        _g(f"KBO{i}", "A", "B", 6, 2, league="KBO") for i in range(10)
    ] + [_g(f"MLB{i}", "A", "B", 0, 10, league="MLB") for i in range(5)]
    elo = EloCalculator.replay("KBO", games)
    ts = TeamStrengthBuilder.build(team="A", league="KBO", results=games, elo=elo)
    # Should reflect the KBO wins, NOT the MLB losses
    assert ts.strength > Decimal('1')


def test_build_to_dict_shape():
    games = [_g(f"G{i}", "A", "B", 5, 4) for i in range(10)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(team="A", league="MLB", results=games, elo=elo)
    d = ts.to_dict()
    assert d["team"] == "A"
    assert d["league"] == "MLB"
    assert "components" in d
    assert "effective_weights" in d


def test_team_strength_is_frozen():
    ts = TeamStrength(
        team="A", league="MLB",
        strength=Decimal('1.2'),
        components=TeamStrengthComponents(pyth=None, form=None, elo=None, pitching=None),
    )
    with pytest.raises(Exception):
        ts.strength = Decimal('99')


def test_pitching_inputs_frozen():
    p = PitchingInputs(
        starter_fip=Decimal('3.5'),
        bullpen_fip=Decimal('3.8'),
        league_fip=Decimal('4.2'),
    )
    with pytest.raises(Exception):
        p.starter_fip = Decimal('99')
