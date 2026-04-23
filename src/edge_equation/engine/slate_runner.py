"""Slate runner: glue between ingestion and the Phase-3 engine."""
from decimal import Decimal
from typing import Optional

from edge_equation.ingestion.schema import Slate, MarketInfo, GameInfo, LEAGUE_TO_SPORT
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Pick, Line


def _league_filter_matches(league: str, filter_value: str) -> bool:
    if filter_value == league:
        return True
    if LEAGUE_TO_SPORT.get(league) == filter_value:
        return True
    return False


def _evaluate_market(game: GameInfo, market: MarketInfo, public_mode: bool) -> Optional[Pick]:
    meta = dict(market.meta or {})
    inputs = meta.get("inputs")
    if inputs is None:
        return None
    universal = meta.get("universal_features", {})

    # Phase 31: forward read_context (and any pitcher / weather / umpire
    # metadata the source attached) into bundle.metadata so the betting
    # engine's _baseline_read can pull real signals instead of the old
    # generic placeholder text.
    bundle_meta: dict = {
        "league": game.league,
        "home_team": game.home_team,
        "away_team": game.away_team,
    }
    if "read_context" in meta:
        bundle_meta["read_context"] = meta["read_context"]
    for k in (
        "pitching_home", "pitching_away",
        "starter_home", "starter_away",
        "weather", "umpire",
        "rest_days_home", "rest_days_away",
        "travel_miles_away", "elo_diff",
        "barrel_rate", "wOBA_delta",
    ):
        if k in meta:
            bundle_meta[k] = meta[k]

    try:
        bundle = FeatureBuilder.build(
            sport=game.sport,
            market_type=market.market_type,
            inputs=inputs,
            universal_features=universal,
            game_id=game.game_id,
            event_time=game.start_time.isoformat(),
            selection=market.selection,
            metadata=bundle_meta,
        )
    except ValueError:
        return None

    line = Line(odds=market.odds if market.odds is not None else -110, number=market.line)
    try:
        return BettingEngine.evaluate(bundle, line, public_mode=public_mode)
    except ValueError:
        return None


def run_slate(slate: Slate, sport: str, public_mode: bool = False) -> list:
    games_by_id = {g.game_id: g for g in slate.games}
    picks = []
    for market in slate.markets:
        game = games_by_id.get(market.game_id)
        if game is None:
            raise ValueError(f"Slate inconsistency: market references unknown game_id {market.game_id!r}")
        if not _league_filter_matches(game.league, sport):
            continue
        pick = _evaluate_market(game, market, public_mode=public_mode)
        if pick is not None:
            picks.append(pick)
    return picks
