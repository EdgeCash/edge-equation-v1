"""Premium pick schema exposed over the API."""
from typing import Optional

from pydantic import BaseModel

from api.schemas.pick import PickOut


class PremiumPickOut(BaseModel):
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
    # Premium additions:
    p10: Optional[str] = None
    p50: Optional[str] = None
    p90: Optional[str] = None
    mean: Optional[str] = None
    notes: Optional[str] = None
