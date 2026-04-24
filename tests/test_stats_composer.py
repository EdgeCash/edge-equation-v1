import math
from decimal import Decimal
import pytest

from edge_equation.stats.composer import ComposedFeatures, FeatureComposer
from edge_equation.stats.elo import EloCalculator, EloRatings, STARTING_RATING
from edge_equation.stats.results import GameResult


def _g(game_id, home, away, hs, as_, start="2026-04-13T18:30:00+09:00", league="KBO"):
    return GameResult(
        result_id=None, game_id=game_id, league=league,
        home_team=home, away_team=away, start_time=start,
        home_score=hs, away_score=as_, status="final",
    )


# --------------------------------------------------- rating_to_strength


def test_starting_rating_maps_to_one():
    assert abs(FeatureComposer.rating_to_strength(STARTING_RATING) - 1.0) < 1e-9


def test_above_avg_rating_maps_above_one():
    s = FeatureComposer.rating_to_strength(Decimal('1700'))
    assert s > 1.0
    # 200 elo points -> e^(200/400) = e^0.5 ~= 1.6487
    assert abs(s - math.exp(0.5)) < 1e-9


def test_below_avg_rating_maps_below_one():
    s = FeatureComposer.rating_to_strength(Decimal('1300'))
    assert s < 1.0


# --------------------------------------------------- compose


def test_compose_empty_history_defaults():
    features = FeatureComposer.compose("A", "B", "KBO", [])
    assert isinstance(features, ComposedFeatures)
    # Phase 31: cold start no longer collapses to exact 1.0 -- strengths
    # get a deterministic per-team seed in +/- 3%. Both still near
    # neutral so Bradley-Terry stays close to a toss-up, just not
    # identically 50/50 (which trips the engine's sanity guard).
    assert abs(features.ml_inputs["strength_home"] - 1.0) < 0.035
    assert abs(features.ml_inputs["strength_away"] - 1.0) < 0.035
    # Two different teams get distinct seeds (deterministic, not RNG).
    assert features.ml_inputs["strength_home"] != features.ml_inputs["strength_away"]
    # Totals default to 1.0 across the board
    assert features.totals_inputs["off_env"] == 1.0
    assert features.totals_inputs["def_env"] == 1.0
    assert features.totals_inputs["pace"] == 1.0
    # home_adv fills a sensible baseline
    assert "home_adv" in features.ml_inputs


def test_compose_strong_team_gets_higher_strength():
    # A wins 20 games straight; A's Elo climbs, so its strength > 1.
    games = [_g(f"G{i}", "A", "B", 10, 2, start=f"2026-04-{1+i:02d}T18:30:00+09:00")
             for i in range(20)]
    features = FeatureComposer.compose("A", "B", "KBO", games)
    assert features.ml_inputs["strength_home"] > features.ml_inputs["strength_away"]


def test_compose_scoped_to_league():
    # KBO and NPB games for the same teams; the composer should only see
    # the KBO half when scoped to KBO.
    games = (
        [_g(f"K{i}", "A", "B", 10, 2, league="KBO") for i in range(10)]
        + [_g(f"N{i}", "A", "B", 1, 9, league="NPB") for i in range(10)]
    )
    kbo = FeatureComposer.compose("A", "B", "KBO", games)
    npb = FeatureComposer.compose("A", "B", "NPB", games)
    assert kbo.ml_inputs["strength_home"] > npb.ml_inputs["strength_home"]


def test_compose_supplied_elo_is_used():
    # Phase 19: strength now blends Pythagorean + form + Elo + pitching.
    # With empty results, Pythagorean/form are unavailable and the Elo
    # component carries the full weight -> strength == rating_to_strength.
    #
    # Rating 1600 maps to exp((1600-1500)/400) = exp(0.25) ~= 1.284
    # which sits cleanly inside the [0.60, 1.60] strength clamp. The
    # prior fixture used 1700 (-> exp(0.5) ~= 1.649) which now gets
    # clamped to 1.60 -- that behavior is separately exercised in
    # test_build_strength_clamped_to_ceiling. This test verifies the
    # unclamped Elo -> strength conversion in its normal range.
    elo = EloRatings(
        league="KBO",
        ratings={"A": Decimal('1600'), "B": Decimal('1500')},
        games_seen={"A": 10, "B": 10},
    )
    features = FeatureComposer.compose("A", "B", "KBO", [], elo=elo)
    assert abs(features.ml_inputs["strength_home"] - math.exp(0.25)) < 1e-4
    # Away team at starting Elo -> strength 1.0.
    assert abs(features.ml_inputs["strength_away"] - 1.0) < 1e-4


def test_compose_to_dict_roundtrip():
    features = FeatureComposer.compose("A", "B", "KBO", [_g("G1", "A", "B", 5, 3)])
    d = features.to_dict()
    assert "ml_inputs" in d
    assert "totals_inputs" in d
    assert "elo" in d
    assert "matchup" in d


# --------------------------------------------------- enrich_markets


def test_enrich_markets_fills_missing_inputs():
    results = [_g(f"G{i}", "A", "B", 5, 3) for i in range(10)]
    raw_games = [{"game_id": "UPCOMING", "league": "KBO",
                  "start_time": "2026-04-20T18:30:00+09:00",
                  "home_team": "A", "away_team": "B"}]
    raw_markets = [
        {"game_id": "UPCOMING", "market_type": "ML", "selection": "A",
         "odds": -140, "line": None, "meta": {}},
        {"game_id": "UPCOMING", "market_type": "Total", "selection": "Over 9",
         "odds": -110, "line": Decimal("9"), "meta": {}},
    ]
    enriched = FeatureComposer.enrich_markets(raw_markets, raw_games, "KBO", results)
    assert len(enriched) == 2
    ml_meta = enriched[0]["meta"]
    total_meta = enriched[1]["meta"]
    assert "strength_home" in ml_meta["inputs"]
    assert "off_env" in total_meta["inputs"]
    # Input lists preserved
    assert raw_markets[0].get("meta") == {}  # original not mutated


def test_enrich_markets_does_not_clobber_existing_inputs():
    results = [_g(f"G{i}", "A", "B", 5, 3) for i in range(5)]
    raw_games = [{"game_id": "UPCOMING", "league": "KBO",
                  "start_time": "2026-04-20T18:30:00+09:00",
                  "home_team": "A", "away_team": "B"}]
    raw_markets = [
        {"game_id": "UPCOMING", "market_type": "ML", "selection": "A",
         "odds": -140, "line": None,
         "meta": {"inputs": {"strength_home": 9.99, "strength_away": 0.01}}},
    ]
    enriched = FeatureComposer.enrich_markets(raw_markets, raw_games, "KBO", results)
    assert enriched[0]["meta"]["inputs"]["strength_home"] == 9.99


def test_enrich_markets_unknown_game_passed_through():
    raw_markets = [{"game_id": "UNKNOWN", "market_type": "ML", "selection": "X",
                    "odds": 100, "line": None, "meta": {}}]
    enriched = FeatureComposer.enrich_markets(raw_markets, [], "KBO", [])
    assert enriched[0]["meta"] == {}


def test_enrich_markets_unsupported_market_type_skipped():
    raw_games = [{"game_id": "G1", "league": "KBO",
                  "start_time": "2026-04-20T18:30:00+09:00",
                  "home_team": "A", "away_team": "B"}]
    raw_markets = [
        {"game_id": "G1", "market_type": "HR", "selection": "X",
         "odds": 320, "line": None, "meta": {}},
    ]
    enriched = FeatureComposer.enrich_markets(raw_markets, raw_games, "KBO", [])
    # HR is a rate-prop; composer only fills ML and Total.
    assert enriched[0]["meta"].get("inputs") is None
