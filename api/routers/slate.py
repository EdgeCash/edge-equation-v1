"""Slate router."""
from typing import List

from fastapi import APIRouter, HTTPException

from api.data_source import slate_entries_for_sport


router = APIRouter(prefix="/slate", tags=["slate"])


@router.get("/{sport}")
def get_slate(sport: str) -> List[dict]:
    try:
        return slate_entries_for_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
