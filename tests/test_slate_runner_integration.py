from datetime import datetime
from decimal import Decimal

import pytest

from edge_equation.ingestion.mlb_source import MlbLikeSource
from edge_equation.ingestion.nba_source import NbaSource
from edge_equation.ingestion.nhl_source import NhlSource
from edge_equation.ingestion.normalizer import normalize_slate
from edge_equation.engine.slate_runner import run_slate
from edge_equation.engine.pick_schema import Pick
from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.scoring import ConfidenceScorer
from edge_equation.ingestion.odds_source import (
    american_to_implied_prob, implied_prob_to_american,
)


RUN = datetime(2026, 4, 20, 9, 0, 0)


def _build_slate(source):
    return normalize_slate(source.get_raw_games(RUN), source.get_raw_markets(RUN))


def test_run_slate_mlb_produces_picks():
    slate = _build_slate(MlbLikeSource("MLB"))
    picks = run_slate(slate, "MLB")
    assert picks
    for p in picks:
        assert isinstance(p, Pick)
        assert p.sport == "MLB"
        assert p.market_type in {"ML", "Total", "K", "HR"}


def test_run_slate_nba_produces_picks():
    # Phase 29: NBA now maps to its own SPORT_CONFIG entry instead of
    # falling through to NCAA_Basketball. Picks tagged with sport='NBA'.
    slate = _build_slate(NbaSource())
    picks = run_slate(slate, "NBA")
    assert picks
    for p in picks:
        assert p.sport == "NBA"


def test_run_slate_nhl_produces_picks():
    slate = _build_slate(NhlSource())
    picks = run_slate(slate, "NHL")
    assert picks
    for p in picks:
        assert p.sport == "NHL"


def test_run_slate_sport_filter_excludes_others():
    mlb = MlbLikeSource("MLB"); nba = NbaSource()
    games = mlb.get_raw_games(RUN) + nba.get_raw_games(RUN)
    markets = mlb.get_raw_markets(RUN) + nba.get_raw_markets(RUN)
    slate = normalize_slate(games, markets)
    mlb_picks = run_slate(slate, "MLB")
    nba_picks = run_slate(slate, "NBA")
    assert all(p.sport == "MLB" for p in mlb_picks)
    # Phase 29: NBA -> "NBA" sport (was "NCAA_Basketball" before).
    assert all(p.sport == "NBA" for p in nba_picks)
    assert {p.game_id for p in mlb_picks} & {p.game_id for p in nba_picks} == set()


def test_run_slate_formula_truth_mlb_ml_first_game():
    slate = _build_slate(MlbLikeSource("MLB"))
    picks = run_slate(slate, "MLB")
    ml_picks = [p for p in picks if p.market_type == "ML"]
    assert ml_picks
    first_ml = ml_picks[0]
    inputs = {"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115}
    universal = {"home_edge": 0.085}
    fv = ProbabilityCalculator.calculate_fair_value("ML", "MLB", inputs, universal)
    expected_fair_prob = fv["fair_prob"]
    expected_edge = EVCalculator.calculate_edge(expected_fair_prob, -132)
    dec_odds = EVCalculator.american_to_decimal(-132)
    expected_kelly_full = EVCalculator.kelly_fraction(expected_edge, dec_odds)
    expected_kelly_half = (expected_kelly_full / Decimal('2')).quantize(Decimal('0.0001'))
    expected_grade = ConfidenceScorer.grade(expected_edge)
    assert first_ml.fair_prob == expected_fair_prob
    assert first_ml.edge == expected_edge
    if expected_edge >= Decimal('0.010000'):
        assert first_ml.kelly == expected_kelly_half
    else:
        assert first_ml.kelly == Decimal('0')
    assert first_ml.grade == expected_grade


def test_run_slate_no_exceptions_across_all_sports():
    all_games = []; all_markets = []
    for src in (MlbLikeSource("MLB"), NbaSource(), NhlSource()):
        all_games += src.get_raw_games(RUN)
        all_markets += src.get_raw_markets(RUN)
    slate = normalize_slate(all_games, all_markets)
    for sport in ("MLB", "NBA", "NHL"):
        picks = run_slate(slate, sport)
        assert isinstance(picks, list)
        for p in picks:
            assert isinstance(p, Pick)


def test_odds_source_american_to_implied_prob_matches_ev_calculator():
    p = american_to_implied_prob(-110)
    direct = Decimal('1') / EVCalculator.american_to_decimal(-110)
    assert p == direct.quantize(Decimal('0.000001'))
    p_pos = american_to_implied_prob(+150)
    direct_pos = Decimal('1') / EVCalculator.american_to_decimal(+150)
    assert p_pos == direct_pos.quantize(Decimal('0.000001'))


def test_odds_source_roundtrip():
    """Note: ±100 share the same implied prob (0.5); skip that boundary."""
    for odds in (-200, -150, -110, +150, +250):
        p = american_to_implied_prob(odds)
        back = implied_prob_to_american(p)
        assert abs(back - odds) <= 1, f"roundtrip failed for {odds}: got {back}"


def test_odds_source_bounds():
    with pytest.raises(ValueError):
        implied_prob_to_american(Decimal('0'))
    with pytest.raises(ValueError):
        implied_prob_to_american(Decimal('1'))
