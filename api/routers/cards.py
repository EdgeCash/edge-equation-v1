"""Cards router."""
from datetime import datetime

from fastapi import APIRouter

from edge_equation.engine.daily_scheduler import generate_daily_edge_card


router = APIRouter(prefix="/cards", tags=["cards"])


@router.get("/daily")
def get_daily_card() -> dict:
    return generate_daily_edge_card(datetime.now())
