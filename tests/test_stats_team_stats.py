from decimal import Decimal
import pytest

from edge_equation.stats.results import GameResult
from edge_equation.stats.team_stats import (
    MatchupFactors,
    N_PRIOR_GAMES,
    TeamRates,
    TeamStats,
)


def _g(game_id, home, away, hs, as_, start="2026-04-13T18:30:00+09:00", league="KBO"):
    return GameResult(
        result_id=None, game_id=game_id, league=league,
        home_team=home, away_team=away, start_time=start,
        home_score=hs, away_score=as_, status="final",
    )


# --------------------------------------------------- league_averages


def test_league_averages_empty_returns_one():
    avgs = TeamStats.league_averages([])
    assert avgs["scoring"] == Decimal('1').quantize(Decimal('0.000001'))
    assert avgs["pace"] == Decimal('1').quantize(Decimal('0.000001'))


def test_league_averages_from_results():
    games = [
        _g("G1", "A", "B", 5, 3),   # 8 total
        _g("G2", "C", "D", 4, 6),   # 10 total
    ]
    avgs = TeamStats.league_averages(games)
    # pace = 18 / 2 games = 9.0; scoring = 18 / 4 team-games = 4.5
    assert avgs["pace"] == Decimal('9.000000')
    assert avgs["scoring"] == Decimal('4.500000')


# --------------------------------------------------- rates_for


def test_rates_new_team_returns_league_average():
    games = [_g(f"G{i}", "A", "B", 5, 3) for i in range(5)]
    rates = TeamStats.rates_for("C", "KBO", games)
    one = Decimal('1').quantize(Decimal('0.000001'))
    assert rates.scoring_rate == one
    assert rates.allowed_rate == one
    assert rates.pace_rate == one


def test_rates_strong_offense_higher_than_average():
    # Build a team A that scores well above league average.
    games = [
        _g(f"GA{i}", "A", "B", 10, 2) for i in range(30)
    ] + [
        _g(f"GB{i}", "C", "D", 4, 4) for i in range(30)
    ]
    rates_a = TeamStats.rates_for("A", "KBO", games)
    # A averages 10 scored per game; league avg is (10+2+4+4)/4 = 5.0
    # sample rate = 10 / 5 = 2.0, shrunk toward 1.0 with 30 games
    assert rates_a.scoring_rate > Decimal('1.0')


def test_rates_weak_defense_higher_allowed_rate():
    games = (
        [_g(f"GA{i}", "A", "B", 2, 10) for i in range(30)]
        + [_g(f"GB{i}", "C", "D", 4, 4) for i in range(30)]
    )
    rates_a = TeamStats.rates_for("A", "KBO", games)
    # A allowed 10 per game; league avg scoring is same as allowed
    assert rates_a.allowed_rate > Decimal('1.0')


def test_rates_shrinkage_toward_one_for_few_games():
    # Team A plays only 2 games (far below N_PRIOR_GAMES=15).
    games = (
        [_g(f"GA{i}", "A", "B", 10, 2) for i in range(2)]
        + [_g(f"GB{i}", "C", "D", 4, 4) for i in range(30)]
    )
    rates_a = TeamStats.rates_for("A", "KBO", games)
    league_avg_scoring = TeamStats.league_averages(games)["scoring"]
    unshrunk = Decimal('10') / league_avg_scoring
    shrink_weight = Decimal(2) / (Decimal(2) + Decimal(N_PRIOR_GAMES))
    expected = shrink_weight * unshrunk + (Decimal('1') - shrink_weight) * Decimal('1')
    assert abs(rates_a.scoring_rate - expected.quantize(Decimal('0.000001'))) < Decimal('0.005')
    # Sanity: shrunk rate is strictly between 1.0 and the unshrunk ratio.
    assert Decimal('1') < rates_a.scoring_rate < unshrunk


def test_rates_different_league_results_ignored():
    games = [
        _g("G1", "A", "B", 10, 2, league="KBO"),
        _g("G2", "A", "B", 1, 1, league="NPB"),
    ]
    rates_a = TeamStats.rates_for("A", "KBO", games)
    # Only the KBO game (10 scored) should contribute to A's KBO rate
    assert rates_a.games == 1


def test_team_rates_frozen():
    rates = TeamRates(
        team="A", league="KBO", games=1,
        scoring_rate=Decimal('1'), allowed_rate=Decimal('1'), pace_rate=Decimal('1'),
    )
    with pytest.raises(Exception):
        rates.games = 99


# --------------------------------------------------- matchup_factors


def test_matchup_factors_league_avg_when_both_new():
    factors = TeamStats.matchup_factors("X", "Y", "KBO", [])
    one = Decimal('1').quantize(Decimal('0.000001'))
    assert factors.off_env == one
    assert factors.def_env == one
    assert factors.pace == one


def test_matchup_factors_high_scoring_pair_vs_league_baseline():
    # A and B are a high-scoring pair (avg total ~14 per game); the rest of
    # the league averages 6 per game. A's off_env and pace should be
    # higher than the overall league average of 1.0.
    games = (
        [_g(f"AB{i}", "A", "B", 8, 6) for i in range(30)]  # A+B avg total 14
        + [_g(f"CD{i}", "C", "D", 3, 3) for i in range(30)]  # C+D avg total 6
    )
    factors = TeamStats.matchup_factors("A", "B", "KBO", games)
    assert factors.off_env > Decimal('1.0')
    assert factors.pace > Decimal('1.0')


def test_matchup_factors_returns_matchup_type():
    factors = TeamStats.matchup_factors("A", "B", "KBO", [_g("G1", "A", "B", 5, 3)])
    assert isinstance(factors, MatchupFactors)
    assert factors.home.team == "A"
    assert factors.away.team == "B"


def test_matchup_factors_to_dict_shape():
    factors = TeamStats.matchup_factors("A", "B", "KBO", [_g("G1", "A", "B", 5, 3)])
    d = factors.to_dict()
    assert "off_env" in d
    assert "home" in d and d["home"]["team"] == "A"
