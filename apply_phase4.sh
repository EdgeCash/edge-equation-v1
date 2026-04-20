#!/usr/bin/env bash
# apply_phase4.sh
#
# Writes Phase-4A ingestion layer modules and tests. Runs pytest.
# Exits non-zero on failure.
#
# Run from repo root of edge-equation-v1 on branch phase-4a-ingestion.

set -euo pipefail

echo "=== Phase 4A: writing ingestion layer modules and tests ==="

ROOT_DIR="$(pwd)"
SRC="$ROOT_DIR/src"
TESTS="$ROOT_DIR/tests"

mkdir -p "$SRC/edge_equation/ingestion"
mkdir -p "$SRC/edge_equation/engine"
mkdir -p "$TESTS"

# Ensure package __init__.py files exist
[ -f "$SRC/edge_equation/ingestion/__init__.py" ] || touch "$SRC/edge_equation/ingestion/__init__.py"

########################################
# ingestion/schema.py
########################################
cat > "$SRC/edge_equation/ingestion/schema.py" << 'EOF'
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
    "NBA": "NCAA_Basketball",
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
EOF

########################################
# ingestion/normalizer.py
########################################
cat > "$SRC/edge_equation/ingestion/normalizer.py" << 'EOF'
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
EOF

########################################
# ingestion/base_source.py
########################################
cat > "$SRC/edge_equation/ingestion/base_source.py" << 'EOF'
"""BaseSource protocol. No network calls, no randomness, pure mock data."""
from datetime import datetime
from typing import Protocol


class BaseSource(Protocol):
    def get_raw_games(self, run_datetime: datetime) -> list: ...
    def get_raw_markets(self, run_datetime: datetime) -> list: ...
EOF

########################################
# ingestion/mlb_source.py
########################################
cat > "$SRC/edge_equation/ingestion/mlb_source.py" << 'EOF'
"""MLB-like source: MLB, KBO, NPB."""
from datetime import datetime, timedelta
from decimal import Decimal


class MlbLikeSource:
    def __init__(self, league: str):
        if league not in ("MLB", "KBO", "NPB"):
            raise ValueError(f"MlbLikeSource supports MLB/KBO/NPB, got {league}")
        self.league = league

    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 13, 5, 0)
        prefix = f"{self.league}-{run_datetime.date().isoformat()}"
        if self.league == "MLB":
            games = [
                {"league": "MLB", "game_id": f"{prefix}-DET-BOS",
                 "start_time": base.isoformat(), "home_team": "BOS", "away_team": "DET"},
                {"league": "MLB", "game_id": f"{prefix}-HOU-CLE",
                 "start_time": (base + timedelta(hours=3)).isoformat(), "home_team": "CLE", "away_team": "HOU"},
                {"league": "MLB", "game_id": f"{prefix}-CIN-TB",
                 "start_time": (base + timedelta(hours=5)).isoformat(), "home_team": "TB", "away_team": "CIN"},
            ]
        elif self.league == "KBO":
            games = [
                {"league": "KBO", "game_id": f"{prefix}-LG-KIA",
                 "start_time": base.isoformat(), "home_team": "KIA", "away_team": "LG"},
                {"league": "KBO", "game_id": f"{prefix}-SSG-LT",
                 "start_time": (base + timedelta(hours=1)).isoformat(), "home_team": "LT", "away_team": "SSG"},
            ]
        else:
            games = [
                {"league": "NPB", "game_id": f"{prefix}-YKT-HNS",
                 "start_time": base.isoformat(), "home_team": "HNS", "away_team": "YKT"},
                {"league": "NPB", "game_id": f"{prefix}-RKT-SFB",
                 "start_time": (base + timedelta(hours=1)).isoformat(), "home_team": "SFB", "away_team": "RKT"},
            ]
        return games

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for idx, g in enumerate(games):
            gid = g["game_id"]; home = g["home_team"]; away = g["away_team"]
            strength_home = 1.32 if idx == 0 else (1.20 + 0.02 * idx)
            strength_away = 1.15 if idx == 0 else (1.10 + 0.01 * idx)
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home,
                "odds": -132 if idx == 0 else -115,
                "meta": {
                    "inputs": {"strength_home": strength_home, "strength_away": strength_away, "home_adv": 0.115},
                    "universal_features": {"home_edge": 0.085},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("9.5"), "odds": -110,
                "meta": {
                    "inputs": {"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "K", "selection": f"{home} SP Over 6.5 K",
                "line": Decimal("6.5"), "odds": -115,
                "meta": {
                    "inputs": {"rate": 7.85},
                    "universal_features": {"matchup_exploit": 0.09, "market_line_delta": 0.08},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "HR", "selection": f"{away} batter Over 0.5 HR",
                "line": Decimal("0.5"), "odds": 320,
                "meta": {
                    "inputs": {"rate": 0.142},
                    "universal_features": {"matchup_exploit": 0.08, "market_line_delta": 0.12},
                },
            })
        return markets
EOF

########################################
# ingestion/nba_source.py
########################################
cat > "$SRC/edge_equation/ingestion/nba_source.py" << 'EOF'
"""NBA source (uses NCAA_Basketball config for math)."""
from datetime import datetime, timedelta
from decimal import Decimal


class NbaSource:
    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 19, 0, 0)
        prefix = f"NBA-{run_datetime.date().isoformat()}"
        return [
            {"league": "NBA", "game_id": f"{prefix}-LAL-BOS",
             "start_time": base.isoformat(), "home_team": "BOS", "away_team": "LAL"},
            {"league": "NBA", "game_id": f"{prefix}-GSW-MIL",
             "start_time": (base + timedelta(hours=1)).isoformat(), "home_team": "MIL", "away_team": "GSW"},
            {"league": "NBA", "game_id": f"{prefix}-DEN-PHX",
             "start_time": (base + timedelta(hours=3)).isoformat(), "home_team": "PHX", "away_team": "DEN"},
        ]

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for g in games:
            gid = g["game_id"]; home = g["home_team"]; away = g["away_team"]
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home, "odds": -140,
                "meta": {
                    "inputs": {"strength_home": 1.30, "strength_away": 1.12, "home_adv": 0.115},
                    "universal_features": {"home_edge": 0.060, "form_off": 0.02},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("225.5"), "odds": -110,
                "meta": {
                    "inputs": {"off_env": 1.05, "def_env": 1.02, "pace": 1.01, "dixon_coles_adj": 0.00},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Points", "selection": f"{home} star Over 26.5",
                "line": Decimal("26.5"), "odds": -115,
                "meta": {
                    "inputs": {"rate": 28.3},
                    "universal_features": {"matchup_exploit": 0.05, "form_off": 0.04},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Rebounds", "selection": f"{away} big Over 8.5",
                "line": Decimal("8.5"), "odds": -120,
                "meta": {
                    "inputs": {"rate": 9.2},
                    "universal_features": {"form_def": -0.03},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Assists", "selection": f"{home} guard Over 6.5",
                "line": Decimal("6.5"), "odds": -110,
                "meta": {
                    "inputs": {"rate": 7.1},
                    "universal_features": {"form_off": 0.02},
                },
            })
        return markets
EOF

########################################
# ingestion/nhl_source.py
########################################
cat > "$SRC/edge_equation/ingestion/nhl_source.py" << 'EOF'
"""NHL source."""
from datetime import datetime, timedelta
from decimal import Decimal


class NhlSource:
    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 19, 30, 0)
        prefix = f"NHL-{run_datetime.date().isoformat()}"
        return [
            {"league": "NHL", "game_id": f"{prefix}-PHI-PIT",
             "start_time": base.isoformat(), "home_team": "PIT", "away_team": "PHI"},
            {"league": "NHL", "game_id": f"{prefix}-BOS-TOR",
             "start_time": (base + timedelta(minutes=30)).isoformat(), "home_team": "TOR", "away_team": "BOS"},
            {"league": "NHL", "game_id": f"{prefix}-COL-VGK",
             "start_time": (base + timedelta(hours=2)).isoformat(), "home_team": "VGK", "away_team": "COL"},
        ]

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for g in games:
            gid = g["game_id"]; home = g["home_team"]
            ml_inputs = {"strength_home": 1.22, "strength_away": 1.10, "home_adv": 0.095}
            ml_univ = {"home_edge": 0.05}
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home, "odds": -125,
                "meta": {"inputs": ml_inputs, "universal_features": ml_univ},
            })
            markets.append({
                "game_id": gid, "market_type": "Puck_Line", "selection": f"{home} -1.5",
                "line": Decimal("-1.5"), "odds": +180,
                "meta": {"inputs": ml_inputs, "universal_features": ml_univ},
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("6.5"), "odds": -105,
                "meta": {
                    "inputs": {"off_env": 1.08, "def_env": 1.04, "pace": 1.02, "dixon_coles_adj": -0.05},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "SOG", "selection": f"{home} star Over 4.5 SOG",
                "line": Decimal("4.5"), "odds": -115,
                "meta": {
                    "inputs": {"rate": 4.12},
                    "universal_features": {"matchup_exploit": 0.10},
                },
            })
        return markets
EOF

########################################
# ingestion/nfl_source.py
########################################
cat > "$SRC/edge_equation/ingestion/nfl_source.py" << 'EOF'
"""NFL source."""
from datetime import datetime, timedelta
from decimal import Decimal


class NflSource:
    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 13, 0, 0)
        prefix = f"NFL-{run_datetime.date().isoformat()}"
        return [
            {"league": "NFL", "game_id": f"{prefix}-KC-BUF",
             "start_time": base.isoformat(), "home_team": "BUF", "away_team": "KC"},
            {"league": "NFL", "game_id": f"{prefix}-DAL-PHI",
             "start_time": (base + timedelta(hours=3, minutes=25)).isoformat(), "home_team": "PHI", "away_team": "DAL"},
            {"league": "NFL", "game_id": f"{prefix}-SF-GB",
             "start_time": (base + timedelta(hours=7, minutes=15)).isoformat(), "home_team": "GB", "away_team": "SF"},
        ]

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for g in games:
            gid = g["game_id"]; home = g["home_team"]; away = g["away_team"]
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home, "odds": -145,
                "meta": {
                    "inputs": {"strength_home": 1.28, "strength_away": 1.14, "home_adv": 0.115},
                    "universal_features": {"home_edge": 0.07},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("47.5"), "odds": -110,
                "meta": {
                    "inputs": {"off_env": 1.03, "def_env": 1.01, "pace": 1.00, "dixon_coles_adj": 0.00},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Passing_Yards",
                "selection": f"{home} QB Over 275.5",
                "line": Decimal("275.5"), "odds": -110,
                "meta": {
                    "inputs": {"rate": 312.4},
                    "universal_features": {"form_off": 0.11, "matchup_strength": 0.09},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Rushing_Yards",
                "selection": f"{away} RB Over 65.5",
                "line": Decimal("65.5"), "odds": -115,
                "meta": {
                    "inputs": {"rate": 78.5},
                    "universal_features": {"form_off": -0.04, "matchup_strength": -0.06},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Receiving_Yards",
                "selection": f"{home} WR Over 80.5",
                "line": Decimal("80.5"), "odds": -110,
                "meta": {
                    "inputs": {"rate": 92.3},
                    "universal_features": {"form_off": 0.07},
                },
            })
        return markets
EOF

########################################
# ingestion/soccer_source.py
########################################
cat > "$SRC/edge_equation/ingestion/soccer_source.py" << 'EOF'
"""Soccer source."""
from datetime import datetime, timedelta
from decimal import Decimal


class SoccerSource:
    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 12, 30, 0)
        prefix = f"SOC-{run_datetime.date().isoformat()}"
        return [
            {"league": "SOC", "game_id": f"{prefix}-MCI-ARS",
             "start_time": base.isoformat(), "home_team": "ARS", "away_team": "MCI",
             "meta": {"competition": "EPL"}},
            {"league": "SOC", "game_id": f"{prefix}-RMA-BAR",
             "start_time": (base + timedelta(hours=3)).isoformat(), "home_team": "BAR", "away_team": "RMA",
             "meta": {"competition": "LaLiga"}},
            {"league": "SOC", "game_id": f"{prefix}-BAY-DOR",
             "start_time": (base + timedelta(hours=5)).isoformat(), "home_team": "DOR", "away_team": "BAY",
             "meta": {"competition": "Bundesliga"}},
        ]

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for g in games:
            gid = g["game_id"]; home = g["home_team"]
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home, "odds": +120,
                "meta": {
                    "inputs": {"strength_home": 1.20, "strength_away": 1.15, "home_adv": 0.10},
                    "universal_features": {"home_edge": 0.06},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("2.5"), "odds": -105,
                "meta": {
                    "inputs": {"off_env": 1.02, "def_env": 1.01, "pace": 1.00, "dixon_coles_adj": -0.03},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "BTTS", "selection": "Yes", "odds": -130,
                "meta": {
                    "inputs": {"home_lambda": 1.35, "away_lambda": 1.20},
                    "universal_features": {"form_off": 0.03},
                },
            })
        return markets
EOF

########################################
# ingestion/odds_source.py
########################################
cat > "$SRC/edge_equation/ingestion/odds_source.py" << 'EOF'
"""Odds utilities matching Phase-2 EVCalculator conventions."""
from decimal import Decimal, ROUND_HALF_UP

from edge_equation.math.ev import EVCalculator


def american_to_implied_prob(odds: int) -> Decimal:
    dec_odds = EVCalculator.american_to_decimal(odds)
    return (Decimal('1') / dec_odds).quantize(Decimal('0.000001'))


def implied_prob_to_american(prob: Decimal) -> int:
    if not isinstance(prob, Decimal):
        prob = Decimal(str(prob))
    if prob <= Decimal('0') or prob >= Decimal('1'):
        raise ValueError(f"prob must be in (0, 1), got {prob}")
    if prob >= Decimal('0.5'):
        val = -(prob * Decimal('100')) / (Decimal('1') - prob)
    else:
        val = ((Decimal('1') - prob) * Decimal('100')) / prob
    return int(val.to_integral_value(rounding=ROUND_HALF_UP))
EOF

########################################
# engine/slate_runner.py
########################################
cat > "$SRC/edge_equation/engine/slate_runner.py" << 'EOF'
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

    try:
        bundle = FeatureBuilder.build(
            sport=game.sport,
            market_type=market.market_type,
            inputs=inputs,
            universal_features=universal,
            game_id=game.game_id,
            event_time=game.start_time.isoformat(),
            selection=market.selection,
            metadata={"league": game.league, "home_team": game.home_team, "away_team": game.away_team},
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
EOF

########################################
# tests/test_ingestion_schema.py
########################################
cat > "$TESTS/test_ingestion_schema.py" << 'EOF'
import pytest
from datetime import datetime
from decimal import Decimal

from edge_equation.ingestion.schema import GameInfo, MarketInfo, Slate


def test_game_info_construction_and_to_dict():
    g = GameInfo(
        sport="MLB", league="MLB", game_id="MLB-2026-04-20-DET-BOS",
        start_time=datetime(2026, 4, 20, 13, 5, 0),
        home_team="BOS", away_team="DET", meta={"weather": "clear"},
    )
    d = g.to_dict()
    assert d["sport"] == "MLB"
    assert d["league"] == "MLB"
    assert d["game_id"] == "MLB-2026-04-20-DET-BOS"
    assert d["start_time"] == "2026-04-20T13:05:00"
    assert d["home_team"] == "BOS"
    assert d["away_team"] == "DET"
    assert d["meta"] == {"weather": "clear"}


def test_game_info_is_frozen():
    g = GameInfo(sport="MLB", league="MLB", game_id="x",
                 start_time=datetime(2026, 1, 1), home_team="A", away_team="B")
    with pytest.raises(Exception):
        g.home_team = "Z"


def test_market_info_construction_and_to_dict():
    m = MarketInfo(game_id="MLB-2026-04-20-DET-BOS", market_type="Total",
                   selection="Over", line=Decimal("9.5"), odds=-110, meta={"source": "mock"})
    d = m.to_dict()
    assert d["game_id"] == "MLB-2026-04-20-DET-BOS"
    assert d["market_type"] == "Total"
    assert d["selection"] == "Over"
    assert d["line"] == "9.5"
    assert d["odds"] == -110
    assert d["meta"] == {"source": "mock"}


def test_market_info_with_no_line_and_no_odds():
    m = MarketInfo(game_id="x", market_type="ML", selection="BOS")
    d = m.to_dict()
    assert d["line"] is None
    assert d["odds"] is None


def test_market_info_is_frozen():
    m = MarketInfo(game_id="x", market_type="ML", selection="BOS")
    with pytest.raises(Exception):
        m.odds = 99


def test_slate_to_dict_and_from_lists():
    g = GameInfo(sport="MLB", league="MLB", game_id="g1",
                 start_time=datetime(2026, 4, 20, 13, 0, 0), home_team="BOS", away_team="DET")
    m = MarketInfo(game_id="g1", market_type="ML", selection="BOS", odds=-132)
    slate = Slate.from_lists([g], [m])
    d = slate.to_dict()
    assert len(d["games"]) == 1
    assert len(d["markets"]) == 1
    assert d["games"][0]["game_id"] == "g1"
    assert d["markets"][0]["selection"] == "BOS"
EOF

########################################
# tests/test_ingestion_normalizer.py
########################################
cat > "$TESTS/test_ingestion_normalizer.py" << 'EOF'
import pytest
from datetime import datetime
from decimal import Decimal

from edge_equation.ingestion.normalizer import normalize_slate
from edge_equation.ingestion.schema import Slate, GameInfo, MarketInfo


def _sample_games():
    return [{
        "league": "MLB", "game_id": "MLB-2026-04-20-DET-BOS",
        "start_time": "2026-04-20T13:05:00", "home_team": "BOS", "away_team": "DET",
        "meta": {"weather": "clear"}, "unknown_field": "should be ignored",
    }]


def _sample_markets():
    return [
        {"game_id": "MLB-2026-04-20-DET-BOS", "market_type": "ML", "selection": "BOS", "odds": -132},
        {"game_id": "MLB-2026-04-20-DET-BOS", "market_type": "Total", "selection": "Over",
         "line": "9.5", "odds": -110},
    ]


def test_normalize_produces_typed_slate():
    slate = normalize_slate(_sample_games(), _sample_markets())
    assert isinstance(slate, Slate)
    assert len(slate.games) == 1
    assert len(slate.markets) == 2
    g = slate.games[0]
    assert isinstance(g, GameInfo)
    assert g.sport == "MLB"
    assert g.league == "MLB"
    assert g.start_time == datetime(2026, 4, 20, 13, 5, 0)
    m0 = slate.markets[0]
    assert isinstance(m0, MarketInfo)
    assert m0.market_type == "ML"
    assert m0.odds == -132


def test_normalize_coerces_line_to_decimal():
    slate = normalize_slate(_sample_games(), _sample_markets())
    total = slate.markets[1]
    assert total.line == Decimal("9.5")
    assert isinstance(total.line, Decimal)


def test_normalize_accepts_datetime_object():
    games = [{"league": "MLB", "game_id": "g1",
              "start_time": datetime(2026, 4, 20, 13, 0, 0),
              "home_team": "BOS", "away_team": "DET"}]
    slate = normalize_slate(games, [])
    assert slate.games[0].start_time == datetime(2026, 4, 20, 13, 0, 0)


def test_normalize_ignores_unknown_fields():
    games = _sample_games()
    games[0]["weird_extra"] = "ignored"
    slate = normalize_slate(games, [])
    g = slate.games[0]
    assert not hasattr(g, "weird_extra")
    assert "weird_extra" not in g.meta


def test_normalize_missing_game_field_raises():
    games = [{"league": "MLB", "game_id": "g1", "start_time": "2026-04-20T13:00:00"}]
    with pytest.raises(ValueError, match="missing required fields"):
        normalize_slate(games, [])


def test_normalize_missing_market_field_raises():
    games = _sample_games()
    markets = [{"game_id": "MLB-2026-04-20-DET-BOS", "market_type": "ML"}]
    with pytest.raises(ValueError, match="missing required fields"):
        normalize_slate(games, markets)


def test_normalize_unknown_league_raises():
    games = [{"league": "CRICKET", "game_id": "g1", "start_time": "2026-04-20T13:00:00",
              "home_team": "A", "away_team": "B"}]
    with pytest.raises(ValueError, match="unknown league"):
        normalize_slate(games, [])


def test_normalize_market_type_invalid_for_league_raises():
    games = _sample_games()
    markets = [{"game_id": "MLB-2026-04-20-DET-BOS",
                "market_type": "Passing_Yards", "selection": "BOS"}]
    with pytest.raises(ValueError, match="not valid for league"):
        normalize_slate(games, markets)


def test_normalize_market_references_unknown_game_raises():
    games = _sample_games()
    markets = [{"game_id": "does-not-exist", "market_type": "ML", "selection": "BOS"}]
    with pytest.raises(ValueError, match="unknown game_id"):
        normalize_slate(games, markets)


def test_normalize_invalid_datetime_raises():
    games = [{"league": "MLB", "game_id": "g1", "start_time": "not-a-date",
              "home_team": "A", "away_team": "B"}]
    with pytest.raises(ValueError, match="Invalid start_time"):
        normalize_slate(games, [])
EOF

########################################
# tests/test_ingestion_sources.py
########################################
cat > "$TESTS/test_ingestion_sources.py" << 'EOF'
import pytest
from datetime import datetime

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
EOF

########################################
# tests/test_slate_runner_integration.py
########################################
cat > "$TESTS/test_slate_runner_integration.py" << 'EOF'
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
    slate = _build_slate(NbaSource())
    picks = run_slate(slate, "NBA")
    assert picks
    for p in picks:
        assert p.sport == "NCAA_Basketball"


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
    assert all(p.sport == "NCAA_Basketball" for p in nba_picks)
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
EOF

echo "=== Phase 4A files written. Running pytest ==="

if command -v pytest >/dev/null 2>&1; then
  if ! pytest -v; then
    echo ""
    echo "ERROR: tests failed." >&2
    exit 1
  fi
else
  echo "WARNING: pytest not installed. Skipping test run."
  echo "  (Tests were verified in sandbox before this script was generated.)"
fi

echo ""
echo "=== Phase 4A complete ==="
