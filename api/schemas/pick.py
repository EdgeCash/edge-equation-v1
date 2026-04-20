"""Pick schema exposed over the API.

Plain JSON-serializable fields. Decimals become strings to preserve
deterministic precision from the engine.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PickOut(BaseModel):
    selection: str
    market_type: str
    sport: str
    line_odds: int
    line_number: Optional[str] = None
    fair_prob: Optional[str] = None
    expected_value: Optional[str] = None
    edge: Optional[str] = None
    grade: str
    kelly: Optional[str] = None
    realization: int
    game_id: Optional[str] = None
    event_time: Optional[str] = None
