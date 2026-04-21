from decimal import Decimal
import pytest

from edge_equation.stats.elo import (
    EloCalculator,
    EloRatings,
    K_FACTOR,
    HFA_RATING,
    STARTING_RATING,
)
from edge_equation.stats.results import GameResult


def _g(game_id, home, away, hs, as_, start="2026-04-13T18:30:00+09:00", league="KBO"):
    return GameResult(
        result_id=None, game_id=game_id, league=league,
        home_team=home, away_team=away, start_time=start,
        home_score=hs, away_score=as_, status="final",
    )


# --------------------------------------------------- expected_score


def test_equal_ratings_gives_05():
    expected = EloCalculator.expected_score(Decimal('1500'), Decimal('1500'))
    assert expected == Decimal('0.500000')


def test_higher_rating_higher_expected_score():
    hi = EloCalculator.expected_score(Decimal('1700'), Decimal('1500'))
    lo = EloCalculator.expected_score(Decimal('1500'), Decimal('1700'))
    assert hi > Decimal('0.5')
    assert lo < Decimal('0.5')
    assert abs((hi + lo) - Decimal('1')) < Decimal('0.000001')


def test_400_point_gap_produces_90_percent():
    # Classic Elo property: 400-point favorite wins ~91% of the time.
    e = EloCalculator.expected_score(Decimal('1900'), Decimal('1500'))
    assert Decimal('0.90') < e < Decimal('0.92')


# --------------------------------------------------- update


def test_win_moves_ratings_correctly():
    new_h, new_a = EloCalculator.update(
        Decimal('1500'), Decimal('1500'),
        home_score=5, away_score=3,
        k=Decimal('20'), hfa=Decimal('0'),
    )
    # Home won from an even matchup, so home rating should rise 10 pts
    # and away should drop the same.
    assert new_h > Decimal('1500')
    assert new_a < Decimal('1500')
    assert (new_h - Decimal('1500')) == -(new_a - Decimal('1500'))


def test_loss_moves_ratings():
    new_h, new_a = EloCalculator.update(
        Decimal('1500'), Decimal('1500'),
        home_score=2, away_score=4, k=Decimal('20'),
    )
    assert new_h < Decimal('1500')
    assert new_a > Decimal('1500')


def test_draw_no_net_movement_on_equal_ratings():
    new_h, new_a = EloCalculator.update(
        Decimal('1500'), Decimal('1500'),
        home_score=3, away_score=3, k=Decimal('20'),
    )
    assert new_h == Decimal('1500').quantize(Decimal('0.000001'))
    assert new_a == Decimal('1500').quantize(Decimal('0.000001'))


def test_hfa_reduces_home_upset_movement():
    # If home has Elo HFA bonus, a home win against an equal team should
    # produce a smaller rating bump because it was more expected.
    bumped_with_hfa = EloCalculator.update(
        Decimal('1500'), Decimal('1500'), 5, 3,
        k=Decimal('20'), hfa=Decimal('60'),
    )
    bumped_without_hfa = EloCalculator.update(
        Decimal('1500'), Decimal('1500'), 5, 3,
        k=Decimal('20'), hfa=Decimal('0'),
    )
    assert bumped_with_hfa[0] < bumped_without_hfa[0]


def test_constants_populated_for_common_leagues():
    for league in ("NFL", "NBA", "NHL", "MLB", "KBO", "NPB", "SOC"):
        assert league in K_FACTOR
        assert league in HFA_RATING
        assert K_FACTOR[league] > Decimal('0')


# --------------------------------------------------- replay


def test_replay_empty_results():
    elo = EloCalculator.replay("KBO", [])
    assert elo.league == "KBO"
    assert elo.ratings == {}
    assert elo.games_seen == {}


def test_replay_single_game_updates_both_teams():
    elo = EloCalculator.replay("KBO", [_g("G1", "Doosan Bears", "LG Twins", 5, 3)])
    assert elo.rating_for("Doosan Bears") > STARTING_RATING
    assert elo.rating_for("LG Twins") < STARTING_RATING
    assert elo.games_for("Doosan Bears") == 1
    assert elo.games_for("LG Twins") == 1


def test_replay_unseen_team_returns_starting_rating():
    elo = EloCalculator.replay("KBO", [_g("G1", "Doosan Bears", "LG Twins", 5, 3)])
    assert elo.rating_for("KIA Tigers") == STARTING_RATING


def test_replay_respects_league_filter():
    games = [
        _g("G1", "A", "B", 5, 3, league="KBO"),
        _g("G2", "A", "B", 5, 3, league="NPB"),
    ]
    elo = EloCalculator.replay("KBO", games)
    # Only the KBO game should affect ratings
    assert elo.games_for("A") == 1


def test_replay_is_deterministic():
    games = [
        _g(f"G{i}", "A", "B", 5, 3, start=f"2026-04-{13+i}T18:30:00+09:00")
        for i in range(5)
    ]
    a = EloCalculator.replay("KBO", games)
    b = EloCalculator.replay("KBO", games)
    assert a.ratings == b.ratings


def test_replay_order_is_chronological():
    # Supply games in reverse order; replay should re-sort before applying
    # so the end state is the same.
    games = [
        _g(f"G{i}", "A", "B", 5, 3, start=f"2026-04-{13+i}T18:30:00+09:00")
        for i in range(5)
    ]
    forward = EloCalculator.replay("KBO", games)
    reversed_in = EloCalculator.replay("KBO", list(reversed(games)))
    assert forward.ratings == reversed_in.ratings


# --------------------------------------------------- win_probability


def test_win_probability_default_hfa():
    elo = EloCalculator.replay("KBO", [])
    # Two fresh teams -> home wins with probability corresponding to HFA
    p = EloCalculator.win_probability("KBO", "A", "B", elo)
    assert p > Decimal('0.5')  # HFA bumps home favorite


def test_win_probability_strong_favorite():
    elo = EloRatings(league="KBO", ratings={"A": Decimal('1800'), "B": Decimal('1400')}, games_seen={})
    p = EloCalculator.win_probability("KBO", "A", "B", elo)
    assert p > Decimal('0.9')


def test_elo_ratings_frozen():
    elo = EloCalculator.replay("KBO", [])
    with pytest.raises(Exception):
        elo.league = "X"


def test_to_dict_shape():
    elo = EloCalculator.replay("KBO", [_g("G1", "A", "B", 5, 3)])
    d = elo.to_dict()
    assert d["league"] == "KBO"
    assert "A" in d["ratings"]
    assert d["games_seen"]["A"] == 1
