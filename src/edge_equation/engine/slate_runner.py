"""Slate runner: glue between ingestion and the Phase-3 engine."""
import os
from decimal import Decimal
from typing import Optional

from edge_equation.ingestion.schema import Slate, MarketInfo, GameInfo, LEAGUE_TO_SPORT
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Pick, Line
from edge_equation.utils.logging import get_logger


_logger = get_logger("edge-equation.slate_runner")


# Process-wide debug counters populated when DEBUG=1. Cheap, additive,
# and safe to ignore in normal runs -- consumers (e.g. the --debug CLI
# summary) read via get_debug_stats() and can reset via reset_debug_stats().
_DEBUG_STATS: dict = {
    "markets_processed": 0,
    "markets_with_picks": 0,
    "supported_markets_seen": set(),
}


def _debug_enabled() -> bool:
    return os.getenv("DEBUG") == "1"


def get_debug_stats() -> dict:
    return {
        "markets_processed": _DEBUG_STATS["markets_processed"],
        "markets_with_picks": _DEBUG_STATS["markets_with_picks"],
        "supported_markets_seen": sorted(_DEBUG_STATS["supported_markets_seen"]),
    }


def reset_debug_stats() -> None:
    _DEBUG_STATS["markets_processed"] = 0
    _DEBUG_STATS["markets_with_picks"] = 0
    _DEBUG_STATS["supported_markets_seen"] = set()


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
    # Local copy so we can enrich without mutating the upstream meta dict
    # and so the downstream math sees market.line / game_id without the
    # composer needing to duplicate them into inputs itself.
    inputs = dict(inputs)
    if market.line is not None and "line" not in inputs:
        # Normalize to a HOME-CENTRIC line before handing to the math
        # layer. MarketInfo.line is outcome-centric (for display): a
        # spread with home -3.5 produces one outcome with line=-3.5 and
        # another with line=+3.5. ProbabilityCalculator's Spread branch,
        # however, assumes the line is from the home team's perspective.
        # If we pass the away-outcome's +3.5 as-is, the calculator
        # produces a home-side fair_prob keyed to the wrong line and
        # home/away picks no longer sum to 1 after mirroring. Flipping
        # sign when the selection is the away team restores symmetry
        # without disturbing display (which still uses market.line).
        is_away_spread = (
            market.market_type in ("Spread", "Run_Line", "Puck_Line")
            and market.selection
            and market.selection.strip() == game.away_team
        )
        inputs["line"] = -market.line if is_away_spread else market.line
    if game.game_id and "game_id" not in inputs:
        inputs["game_id"] = game.game_id
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
    except ValueError as exc:
        _logger.warning(f"Dropped market {market.market_type} for {market.game_id}: {exc}")
        return None

    line = Line(odds=market.odds if market.odds is not None else -110, number=market.line)
    try:
        return BettingEngine.evaluate(bundle, line, public_mode=public_mode)
    except ValueError as exc:
        _logger.warning(f"Dropped market {market.market_type} for {market.game_id}: {exc}")
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
        if _debug_enabled():
            _DEBUG_STATS["markets_processed"] += 1
            if pick is not None:
                _DEBUG_STATS["markets_with_picks"] += 1
                _DEBUG_STATS["supported_markets_seen"].add(pick.market_type)
                print(
                    f"[OUTPUT] Produced pick: {pick.market_type} | "
                    f"{pick.game_id} | Edge: {getattr(pick, 'edge', 'N/A')}"
                )
            else:
                print(f"[SKIPPED] Market: {market.market_type}")
        if pick is not None:
            picks.append(pick)
    return picks
