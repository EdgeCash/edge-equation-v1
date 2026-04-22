"""
Daily scheduler.

Light orchestration with stubbed game data. No API calls.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.posting.posting_formatter import PostingFormatter


_MORNING_STUB = [
    {
        "sport": "MLB",
        "market_type": "ML",
        "selection": "BOS",
        "game_id": "MLB-2026-04-20-DET-BOS",
        "event_time": "2026-04-20T13:05:00-04:00",
        "inputs": {"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        "universal_features": {"home_edge": 0.085},
        "line": {"odds": -132},
    },
    {
        "sport": "MLB",
        "market_type": "Total",
        "selection": "Over 9.5",
        "game_id": "MLB-2026-04-20-DET-BOS",
        "event_time": "2026-04-20T13:05:00-04:00",
        "inputs": {"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        "universal_features": {},
        "line": {"odds": -110, "number": "9.5"},
    },
]

_EVENING_STUB = [
    {
        "sport": "NHL",
        "market_type": "SOG",
        "selection": "Crosby Over 4.5 SOG",
        "game_id": "NHL-2026-04-20-PHI-PIT",
        "event_time": "2026-04-20T19:30:00-04:00",
        "inputs": {"rate": 4.12},
        "universal_features": {"matchup_exploit": 0.10},
        "line": {"odds": -115, "number": "4.5"},
    },
]


def _pick_from_stub(stub: dict, public_mode: bool = False):
    bundle = FeatureBuilder.build(
        sport=stub["sport"],
        market_type=stub["market_type"],
        inputs=stub["inputs"],
        universal_features=stub["universal_features"],
        game_id=stub.get("game_id"),
        event_time=stub.get("event_time"),
        selection=stub.get("selection"),
    )
    line_raw = stub["line"]
    number = Decimal(str(line_raw["number"])) if "number" in line_raw else None
    line = Line(odds=int(line_raw["odds"]), number=number)
    return BettingEngine.evaluate(bundle, line, public_mode=public_mode)


def generate_daily_edge_card(run_datetime: datetime, public_mode: bool = False) -> dict:
    picks = [_pick_from_stub(s, public_mode=public_mode) for s in _MORNING_STUB]
    # The legacy Phase-3 scheduler feeds mock stub picks whose grades
    # won't clear the Phase-20 Grade A/A+ filter. Pass skip_filter=True
    # so this development entry point keeps working; the Phase 12
    # ScheduledRunner (production path) hits the filter naturally.
    return PostingFormatter.build_card(
        card_type="daily_edge",
        picks=picks,
        generated_at=run_datetime.isoformat(),
        skip_filter=True,
    )


def generate_evening_edge_card(run_datetime: datetime, public_mode: bool = False) -> dict:
    picks = [_pick_from_stub(s, public_mode=public_mode) for s in _EVENING_STUB]
    return PostingFormatter.build_card(
        card_type="evening_edge",
        picks=picks,
        generated_at=run_datetime.isoformat(),
    )
