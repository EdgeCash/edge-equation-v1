"""
Ingestion schema.

Frozen dataclasses that represent a normalized slate produced by the
ingestion layer. These feed directly into the Phase-3 engine.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


VALID_LEAGUES = {"MLB", "KBO", "NPB", "NBA", "NCAAB", "NHL", "NFL", "NCAAF", "SOC"}

LEAGUE_TO_SPORT = {
    "MLB": "MLB",
    "KBO": "KBO",
    "NPB": "NPB",
    # Phase 29 fix: NBA used to be mapped to "NCAA_Basketball" because
    # NBA wasn't in SPORT_CONFIG yet. That mapping caused NBA games to
    # inherit NCAA's home_adv / Pythagorean exponent, AND -- worse --
    # to compute strength ratings against college-basketball game
    # results, producing wild ratios like 4.22 vs 0.24 on a Knicks /
    # Hawks matchup. With NBA now configured (Phase 27b), point it at
    # its own sport so the engine grades NBA on NBA history.
    "NBA": "NBA",
    "NCAAB": "NCAA_Basketball",
    "NHL": "NHL",
    "NFL": "NFL",
    "NCAAF": "NCAA_Football",
    "SOC": "Soccer",
}


@dataclass(frozen=True)
class GameInfo:
    sport: str
    league: str
    game_id: str
    start_time: datetime
    home_team: str
    away_team: str
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "league": self.league,
            "game_id": self.game_id,
            "start_time": self.start_time.isoformat(),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class MarketInfo:
    game_id: str
    market_type: str
    selection: str
    line: Optional[Decimal] = None
    odds: Optional[int] = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "market_type": self.market_type,
            "selection": self.selection,
            "line": str(self.line) if self.line is not None else None,
            "odds": self.odds,
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class Slate:
    games: tuple
    markets: tuple

    def to_dict(self) -> dict:
        return {
            "games": [g.to_dict() for g in self.games],
            "markets": [m.to_dict() for m in self.markets],
        }

    @staticmethod
    def from_lists(games: list, markets: list) -> "Slate":
        return Slate(games=tuple(games), markets=tuple(markets))
