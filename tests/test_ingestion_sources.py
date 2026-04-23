import pytest
from datetime import datetime
from decimal import Decimal

from edge_equation.ingestion.mlb_source import MlbLikeSource
from edge_equation.ingestion.nba_source import NbaSource
from edge_equation.ingestion.nhl_source import NhlSource
from edge_equation.ingestion.nfl_source import NflSource
from edge_equation.ingestion.soccer_source import SoccerSource
from edge_equation.ingestion.normalizer import normalize_slate, LEAGUE_MARKETS


RUN = datetime(2026, 4, 20, 9, 0, 0)


def _check_source(source, league):
    games = source.get_raw_games(RUN)
    markets = source.get_raw_markets(RUN)
    assert len(games) >= 2, f"{league}: expected >= 2 games"
    assert len(markets) >= 1, f"{league}: expected >= 1 market"
    game_ids = {g["game_id"] for g in games}
    for m in markets:
        assert m["game_id"] in game_ids, f"{league}: market references unknown game_id {m['game_id']}"
        assert m["market_type"] in LEAGUE_MARKETS[league], \
            f"{league}: market_type {m['market_type']} not valid for league"
    slate = normalize_slate(games, markets)
    assert len(slate.games) == len(games)
    assert len(slate.markets) == len(markets)


def test_mlb_source(): _check_source(MlbLikeSource("MLB"), "MLB")
def test_kbo_source(): _check_source(MlbLikeSource("KBO"), "KBO")
def test_npb_source(): _check_source(MlbLikeSource("NPB"), "NPB")
def test_nba_source(): _check_source(NbaSource(), "NBA")
def test_nhl_source(): _check_source(NhlSource(), "NHL")
def test_nfl_source(): _check_source(NflSource(), "NFL")
def test_soccer_source(): _check_source(SoccerSource(), "SOC")


def test_mlb_source_rejects_invalid_league():
    with pytest.raises(ValueError):
        MlbLikeSource("NBA")


def test_sources_are_deterministic():
    s1 = MlbLikeSource("MLB"); s2 = MlbLikeSource("MLB")
    assert s1.get_raw_games(RUN) == s2.get_raw_games(RUN)
    assert s1.get_raw_markets(RUN) == s2.get_raw_markets(RUN)


# ---------------------------------------------------------------------------
# Explicit coverage that the newer market branches are actually emitted
# so a future refactor that drops them gets caught.
# ---------------------------------------------------------------------------

def _markets_by_type_for(source):
    by_type: dict = {}
    for m in source.get_raw_markets(RUN):
        by_type.setdefault(m["market_type"], []).append(m)
    return by_type


def _assert_home_away_spread_pair(markets, home_team, away_team, home_line, away_line):
    """Helper: a spread-family market should emit both outcomes from the
    same game with opposite-signed lines so end-to-end CI exercises the
    home/away mirroring path in BettingEngine."""
    sels = {m["selection"]: m for m in markets}
    assert home_team in sels, f"missing home outcome for home={home_team}"
    assert away_team in sels, f"missing away outcome for away={away_team}"
    assert sels[home_team]["line"] == home_line
    assert sels[away_team]["line"] == away_line


def test_mlb_source_emits_run_line_both_sides():
    source = MlbLikeSource("MLB")
    by_type = _markets_by_type_for(source)
    assert "Run_Line" in by_type
    # 3 MLB games × 2 sides = 6 Run_Line outcomes.
    assert len(by_type["Run_Line"]) == 6
    # Spot-check one game: home -1.5 and away +1.5.
    games = source.get_raw_games(RUN)
    first = games[0]
    per_game = [m for m in by_type["Run_Line"] if m["game_id"] == first["game_id"]]
    _assert_home_away_spread_pair(
        per_game, first["home_team"], first["away_team"],
        Decimal("-1.5"), Decimal("1.5"),
    )


def test_mlb_source_emits_nrfi_and_yrfi():
    source = MlbLikeSource("MLB")
    by_type = _markets_by_type_for(source)
    assert "NRFI" in by_type and "YRFI" in by_type
    nrfi = by_type["NRFI"][0]
    yrfi = by_type["YRFI"][0]
    assert nrfi["selection"] == "No"
    assert yrfi["selection"] == "Yes"
    # Both carry first-inning lambdas for the calculator.
    assert "home_lambda" in nrfi["meta"]["inputs"]
    assert "away_lambda" in nrfi["meta"]["inputs"]


def test_kbo_and_npb_emit_run_line():
    """Baseball-family sources share logic; KBO/NPB must get Run_Line too."""
    for league in ("KBO", "NPB"):
        by_type = _markets_by_type_for(MlbLikeSource(league))
        assert "Run_Line" in by_type, f"{league} missing Run_Line"
        assert "NRFI" in by_type and "YRFI" in by_type, \
            f"{league} missing NRFI/YRFI"


def test_nba_source_emits_spread_both_sides():
    source = NbaSource()
    by_type = _markets_by_type_for(source)
    assert "Spread" in by_type
    assert len(by_type["Spread"]) == 6  # 3 games × 2 sides
    games = source.get_raw_games(RUN)
    first = games[0]
    per_game = [m for m in by_type["Spread"] if m["game_id"] == first["game_id"]]
    _assert_home_away_spread_pair(
        per_game, first["home_team"], first["away_team"],
        Decimal("-5.5"), Decimal("5.5"),
    )


def test_nfl_source_emits_spread_both_sides():
    source = NflSource()
    by_type = _markets_by_type_for(source)
    assert "Spread" in by_type
    assert len(by_type["Spread"]) == 6
    games = source.get_raw_games(RUN)
    first = games[0]
    per_game = [m for m in by_type["Spread"] if m["game_id"] == first["game_id"]]
    _assert_home_away_spread_pair(
        per_game, first["home_team"], first["away_team"],
        Decimal("-3.5"), Decimal("3.5"),
    )


# ---------------------------------------------------------------------------
# End-to-end: run a mock slate through run_slate and verify that
# Spread-family picks on both sides of a game (1) both grade and
# (2) their fair_probs sum to 1. The second invariant requires
# slate_runner to normalize the outcome-centric MarketInfo.line into
# a home-centric line before the math layer sees it. Otherwise
# feeding the away-outcome's +3.5 as-is into ProbabilityCalculator
# (which assumes home-centric input) breaks the complement.
# ---------------------------------------------------------------------------

def _assert_spread_picks_are_complementary(picks, market_type):
    from decimal import Decimal as D
    by_game = {}
    for p in picks:
        if p.market_type != market_type:
            continue
        by_game.setdefault(p.game_id, []).append(p)
    assert by_game, f"expected at least one {market_type} pick"
    for gid, pair in by_game.items():
        assert len(pair) == 2, \
            f"game {gid}: {len(pair)} {market_type} picks, expected 2"
        fps = [p.fair_prob for p in pair]
        assert all(fp is not None for fp in fps), \
            f"game {gid}: a {market_type} pick is ungradeable ({pair})"
        total = fps[0] + fps[1]
        assert abs(total - D("1")) < D("0.00001"), \
            f"game {gid}: {market_type} fair_probs {fps} don't sum to 1"


def test_run_slate_nfl_spread_picks_are_complementary():
    from edge_equation.engine.slate_runner import run_slate
    source = NflSource()
    slate = normalize_slate(
        source.get_raw_games(RUN), source.get_raw_markets(RUN),
    )
    _assert_spread_picks_are_complementary(
        run_slate(slate, sport="NFL", public_mode=False), "Spread",
    )


def test_run_slate_nba_spread_picks_are_complementary():
    from edge_equation.engine.slate_runner import run_slate
    source = NbaSource()
    slate = normalize_slate(
        source.get_raw_games(RUN), source.get_raw_markets(RUN),
    )
    _assert_spread_picks_are_complementary(
        run_slate(slate, sport="NBA", public_mode=False), "Spread",
    )


def test_run_slate_mlb_run_line_picks_are_complementary():
    from edge_equation.engine.slate_runner import run_slate
    source = MlbLikeSource("MLB")
    slate = normalize_slate(
        source.get_raw_games(RUN), source.get_raw_markets(RUN),
    )
    _assert_spread_picks_are_complementary(
        run_slate(slate, sport="MLB", public_mode=False), "Run_Line",
    )
