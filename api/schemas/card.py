"""Card schema exposed over the API."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class CardOut(BaseModel):
    card_type: str
    headline: str
    subhead: str
    picks: List[dict]
    tagline: str
    generated_at: Optional[str] = None
