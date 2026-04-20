"""Normalizer: raw dicts -> typed Slate."""
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from edge_equation.ingestion.schema import (
    GameInfo, MarketInfo, Slate, VALID_LEAGUES, LEAGUE_TO_SPORT,
)

LEAGUE_MARKETS = {
    "MLB":   {"ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"},
    "KBO":   {"ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"},
    "NPB":   {"ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"},
    "NBA":   {"ML", "Spread", "Total", "Points", "Rebounds", "Assists"},
    "NCAAB": {"ML", "Spread", "Total", "Points", "Rebounds", "Assists"},
    "NHL":   {"ML", "Puck_Line", "Total", "SOG"},
    "NFL":   {"ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards", "Receiving_Yards"},
    "NCAAF": {"ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards"},
    "SOC":   {"ML", "Total", "BTTS"},
}

_REQUIRED_GAME_FIELDS = ("league", "game_id", "start_time", "home_team", "away_team")
_REQUIRED_MARKET_FIELDS = ("game_id", "market_type", "selection")


def _parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as e:
            raise ValueError(f"Invalid start_time: {value!r} ({e})")
    raise ValueError(f"start_time must be datetime or ISO string, got {type(value).__name__}")


def _validate_required(row: dict, required: tuple, row_kind: str, row_id) -> None:
    missing = [k for k in required if k not in row]
    if missing:
        raise ValueError(f"{row_kind} (id={row_id!r}) missing required fields: {missing}")


def _normalize_game(raw: dict) -> GameInfo:
    _validate_required(raw, _REQUIRED_GAME_FIELDS, "GameInfo", raw.get("game_id"))
    league = raw["league"]
    if league not in VALID_LEAGUES:
        raise ValueError(
            f"GameInfo (id={raw.get('game_id')!r}): unknown league {league!r}. "
            f"Valid: {sorted(VALID_LEAGUES)}"
        )
    sport = raw.get("sport") or LEAGUE_TO_SPORT[league]
    return GameInfo(
        sport=sport,
        league=league,
        game_id=str(raw["game_id"]),
        start_time=_parse_datetime(raw["start_time"]),
        home_team=str(raw["home_team"]),
        away_team=str(raw["away_team"]),
        meta=dict(raw.get("meta", {})),
    )


def _normalize_market(raw: dict, known_game_ids: set, games_by_id: dict) -> MarketInfo:
    _validate_required(raw, _REQUIRED_MARKET_FIELDS, "MarketInfo", raw.get("game_id"))
    game_id = str(raw["game_id"])
    if game_id not in known_game_ids:
        raise ValueError(f"MarketInfo references unknown game_id {game_id!r}")
    market_type = raw["market_type"]
    league = games_by_id[game_id].league
    allowed = LEAGUE_MARKETS.get(league, set())
    if market_type not in allowed:
        raise ValueError(
            f"MarketInfo (game_id={game_id!r}): market_type {market_type!r} "
            f"not valid for league {league!r}. Allowed: {sorted(allowed)}"
        )
    line = raw.get("line")
    if line is not None and not isinstance(line, Decimal):
        line = Decimal(str(line))
    odds = raw.get("odds")
    if odds is not None:
        odds = int(odds)
    return MarketInfo(
        game_id=game_id,
        market_type=market_type,
        selection=str(raw["selection"]),
        line=line,
        odds=odds,
        meta=dict(raw.get("meta", {})),
    )


def normalize_slate(raw_games: list, raw_markets: list) -> Slate:
    games = [_normalize_game(g) for g in raw_games]
    games_by_id = {g.game_id: g for g in games}
    known_ids = set(games_by_id.keys())
    markets = [_normalize_market(m, known_ids, games_by_id) for m in raw_markets]
    return Slate.from_lists(games, markets)
