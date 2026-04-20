"""Picks router."""
from typing import List

from fastapi import APIRouter

from api.data_source import picks_for_today, pick_to_out_dict


router = APIRouter(prefix="/picks", tags=["picks"])


@router.get("/today")
def get_picks_today() -> List[dict]:
    picks = picks_for_today()
    return [pick_to_out_dict(p) for p in picks]
