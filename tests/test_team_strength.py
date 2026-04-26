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


def test_build_no_data_returns_seed_near_neutral():
    """Phase 31: cold start no longer returns the literal NEUTRAL_STRENGTH
    1.0. A flat 1.0 on both sides collapses BT to 50/50, which trips the
    sanity guard for the away pick post-side-flip and zeros the slate.
    The seed perturbation is small (~3%) but non-zero so each team gets
    a deterministic, distinct cold-start strength."""
    ts = TeamStrengthBuilder.build(team="A", league="MLB", results=[])
    assert ts.games_used == 0
    # Strength is within +/- 3% of neutral (the seed perturbation cap).
    assert abs(ts.strength - NEUTRAL_STRENGTH) <= Decimal("0.030001")
    # Two different teams get DIFFERENT seeds (the whole point).
    ts_b = TeamStrengthBuilder.build(team="B", league="MLB", results=[])
    assert ts.strength != ts_b.strength
    # Same team in same league reproduces (deterministic from sha256).
    ts_a2 = TeamStrengthBuilder.build(team="A", league="MLB", results=[])
    assert ts.strength == ts_a2.strength


def test_build_strong_team_returns_strength_above_one():
    games = [_g(f"G{i}", "A", "B", 8, 2) for i in range(20)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    # Tango shrinkage at form_window=15 caps a dominant team well below
    # the old clamp ceiling. Pre-shrinkage this assertion was > 1.5;
    # with shrinkage a 15-game window of comically lopsided wins lands
    # around 1.30-1.40 -- still clearly above neutral, but not pinned.
    assert ts.strength > NEUTRAL_STRENGTH
    assert ts.strength < STRENGTH_CEIL
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


def test_strength_clamp_bounds_are_narrow_enough_to_tame_thin_data():
    """Regression guard: the clamp range was [0.10, 10.00] in early
    development, which let thin-sample strengths inflate to 10x the
    league average and produced phantom 25-28% edges on the Apr 24
    Premium Daily email. Narrowing to [0.60, 1.60] caps a home
    favorite's projected ML probability at roughly 65% vs a
    league-average opponent -- realistic, and cheap insurance against
    cold-start over-confidence. If these bounds widen again the
    over-confidence pathology will return, so pin the exact values
    here rather than just checking that clamping happens."""
    assert STRENGTH_FLOOR == Decimal('0.60'), (
        "STRENGTH_FLOOR should be 0.60; widening below this lets "
        "thin-sample weak-team strengths create phantom edges."
    )
    assert STRENGTH_CEIL == Decimal('1.60'), (
        "STRENGTH_CEIL should be 1.60; widening above this lets "
        "thin-sample strong-team strengths create phantom edges."
    )


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


# -------------------------------------------------- Tango shrinkage
#
# Apr 25, 2026: the engine produced 12 A+ picks of 18 on Premium Daily,
# with median |fair_prob - market_implied| ~14pp -- absurd against sharp
# closing lines. Replay diagnostic in tools/diagnostics/shrinkage_replay.py
# shows Tango Bayesian shrinkage at n=15 collapses A+ to 2 and median
# disagreement to ~2pp. These tests pin that behavior in place.


def test_tango_shrink_passes_through_at_zero_observations():
    # n=0 -> no form-window data, but blended strength may still encode
    # Elo / pitching signal. Helper returns input unchanged in that case;
    # the cold-start branch in build() handles the "truly no data" path
    # before the shrinkage helper is called.
    s = TeamStrengthBuilder._tango_shrink(Decimal('1.50'), n_games=0)
    assert s == Decimal('1.50')


def test_tango_shrink_compresses_clamp_extremes_at_window_size():
    # The Apr 25 numerics that motivated this fix. With form_window=15
    # and k=70, raw 0.60 / 1.60 (the clamp boundaries) compress to
    # roughly 0.916 / 1.083 -- a 1.18x ratio instead of 2.67x.
    low = TeamStrengthBuilder._tango_shrink(Decimal('0.60'), n_games=15)
    high = TeamStrengthBuilder._tango_shrink(Decimal('1.60'), n_games=15)
    assert Decimal('0.85') < low < Decimal('0.95'), low
    assert Decimal('1.05') < high < Decimal('1.15'), high
    ratio = high / low
    assert ratio < Decimal('1.30'), f"expected ratio < 1.30, got {ratio}"


def test_tango_shrink_softens_with_more_data():
    # As n grows, shrinkage softens and the strength approaches the
    # observed value. By n=200 the data dominates the prior.
    raw = Decimal('1.60')
    s_15 = TeamStrengthBuilder._tango_shrink(raw, n_games=15)
    s_50 = TeamStrengthBuilder._tango_shrink(raw, n_games=50)
    s_200 = TeamStrengthBuilder._tango_shrink(raw, n_games=200)
    assert s_15 < s_50 < s_200 < raw


def test_tango_shrink_neutral_strength_unchanged():
    # A team already at neutral (strength 1.0) stays at neutral.
    s = TeamStrengthBuilder._tango_shrink(NEUTRAL_STRENGTH, n_games=15)
    assert abs(s - NEUTRAL_STRENGTH) < Decimal('0.001')


def test_tango_shrink_handles_pathological_input():
    # Zero / negative strength shouldn't crash; falls back to neutral.
    s = TeamStrengthBuilder._tango_shrink(Decimal('0'), n_games=15)
    assert s == NEUTRAL_STRENGTH


def test_build_applies_shrinkage_so_dominant_team_no_longer_pins_clamp():
    # 30 games of comically lopsided 20-0 wins -- the same scenario that,
    # pre-shrinkage, would pin a team at exactly STRENGTH_CEIL. With
    # shrinkage on a 15-game form window the strength now lands inside
    # the clamp range; the clamp becomes a safety net, not the answer.
    games = [_g(f"G{i}", "A", "B", 20, 0) for i in range(30)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    # The exact ceiling under shrinkage depends on the blend weights;
    # for MLB with form_window=15 it sits around 1.40, decisively below
    # the 1.60 clamp.
    assert ts.strength < STRENGTH_CEIL, (
        f"expected strength below the {STRENGTH_CEIL} ceiling after "
        f"Tango shrinkage, got {ts.strength} (pinning the clamp again)"
    )
    assert ts.strength < Decimal('1.50'), (
        f"expected strength under 1.50 after shrinkage; got {ts.strength}. "
        f"If this regresses the clamp is doing the engine's work again."
    )
    assert ts.strength > NEUTRAL_STRENGTH, (
        "should still be above neutral -- the team did dominate"
    )


def test_build_applies_shrinkage_so_weak_team_no_longer_pins_floor():
    games = [_g(f"G{i}", "A", "B", 0, 20) for i in range(30)]
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    assert ts.strength > STRENGTH_FLOOR, (
        f"expected strength above the {STRENGTH_FLOOR} floor after "
        f"Tango shrinkage, got {ts.strength} (pinning the clamp again)"
    )
    assert ts.strength > Decimal('0.65'), (
        f"expected strength above 0.65 after shrinkage; got {ts.strength}"
    )
    assert ts.strength < NEUTRAL_STRENGTH


def test_apr_25_replay_clamped_inputs_produce_realistic_bt_prob():
    """The Apr 25 Premium Daily had Phillies @ Braves with str(H)=1.60
    and str(A)=0.60 -- both clamps fully pinned. Pre-shrinkage Bradley-
    Terry produced a 75% home win probability, which the engine then
    priced as an 18% edge against a -130 closing line. With Tango
    shrinkage at n=15, the same inputs should compress to roughly
    (1.08, 0.92) and BT should land near 57% -- a realistic favorite
    rate that disagrees with the market by only 1-2pp.

    This is the regression test for the over-confidence pathology.
    If shrinkage gets weakened or removed, this test fires loud.
    """
    raw_h = Decimal('1.60')
    raw_a = Decimal('0.60')
    # The form window is 15 for MLB; that's `n` in the Tango formula.
    sh = TeamStrengthBuilder._tango_shrink(raw_h, n_games=15)
    sa = TeamStrengthBuilder._tango_shrink(raw_a, n_games=15)
    # Shrunk strengths land in a tight band.
    assert Decimal('1.05') < sh < Decimal('1.15'), sh
    assert Decimal('0.85') < sa < Decimal('0.95'), sa
    # BT win prob with MLB home_adv=0.115 should now be in the 55-60%
    # range, not the 75% the engine was producing without shrinkage.
    home_adv = math.exp(0.115)
    bt_home = (float(sh) * home_adv) / (float(sh) * home_adv + float(sa))
    assert 0.54 < bt_home < 0.62, (
        f"BT home prob after shrinkage should land in 54-62% for clamped "
        f"raw inputs (1.60, 0.60); got {bt_home:.4f}. Pre-shrinkage this "
        f"was 0.7495 -- the exact overconfidence the Apr 25 slate exposed."
    )
