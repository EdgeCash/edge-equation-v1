"""Premium router."""
from datetime import datetime
from typing import List

from fastapi import APIRouter

from api.data_source import premium_picks_for_today, premium_pick_to_out_dict
from edge_equation.premium.premium_cards import build_premium_daily_edge_card


router = APIRouter(prefix="/premium", tags=["premium"])


@router.get("/picks/today")
def get_premium_picks_today() -> List[dict]:
    premium = premium_picks_for_today()
    return [premium_pick_to_out_dict(pp) for pp in premium]


@router.get("/cards/daily")
def get_premium_card_daily() -> dict:
    premium = premium_picks_for_today()
    card = build_premium_daily_edge_card(premium)
    card.setdefault("generated_at", datetime.now().isoformat())
    return card
