"""
Archive endpoints.

Reads persisted slates, picks, and realization stats from the SQLite DB the
scheduled runner writes to. These are the data sources the Phase 15 website
consumes -- `/cards/daily` and `/picks/today` remain as live-compute mock
preview endpoints for dev.
"""
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from edge_equation.engine.realization import RealizationTracker
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore


router = APIRouter(prefix="/archive", tags=["archive"])


VALID_CARD_TYPES = ("daily_edge", "evening_edge")


def _open_db():
    conn = Database.open(Database.resolve_path(None))
    Database.migrate(conn)
    return conn


def _slate_summary(slate: SlateRecord, n_picks: int) -> dict:
    return {
        "slate_id": slate.slate_id,
        "generated_at": slate.generated_at,
        "sport": slate.sport,
        "card_type": slate.card_type,
        "n_picks": n_picks,
        "metadata": dict(slate.metadata),
    }


@router.get("/slates")
def list_slates(
    limit: int = Query(default=50, ge=1, le=500),
    card_type: Optional[str] = Query(default=None),
) -> List[dict]:
    """Recent slates, newest first. Optionally filtered by card_type."""
    conn = _open_db()
    try:
        if card_type:
            if card_type not in VALID_CARD_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"card_type must be one of {VALID_CARD_TYPES}",
                )
            slates = SlateStore.list_by_card_type(conn, card_type, limit=limit)
        else:
            slates = SlateStore.list_recent(conn, limit=limit)
        out: List[dict] = []
        for s in slates:
            picks = PickStore.list_by_slate(conn, s.slate_id)
            out.append(_slate_summary(s, len(picks)))
        return out
    finally:
        conn.close()


@router.get("/slates/latest")
def latest_slate(
    card_type: str = Query(default="daily_edge"),
) -> dict:
    """Most recent slate of the given card_type. 404 if none exist."""
    if card_type not in VALID_CARD_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"card_type must be one of {VALID_CARD_TYPES}",
        )
    conn = _open_db()
    try:
        slates = SlateStore.list_by_card_type(conn, card_type, limit=1)
        if not slates:
            raise HTTPException(status_code=404, detail=f"no {card_type} slates yet")
        slate = slates[0]
        picks = PickStore.list_by_slate(conn, slate.slate_id)
        return {
            **_slate_summary(slate, len(picks)),
            "picks": [p.to_dict() for p in picks],
        }
    finally:
        conn.close()


@router.get("/slates/{slate_id}")
def get_slate(slate_id: str) -> dict:
    """Full slate detail: metadata + every persisted pick."""
    conn = _open_db()
    try:
        slate = SlateStore.get(conn, slate_id)
        if slate is None:
            raise HTTPException(status_code=404, detail=f"slate {slate_id} not found")
        picks = PickStore.list_by_slate(conn, slate_id)
        return {
            **_slate_summary(slate, len(picks)),
            "picks": [p.to_dict() for p in picks],
        }
    finally:
        conn.close()


@router.get("/hit-rate")
def hit_rate_by_grade(
    sport: Optional[str] = Query(default=None),
) -> dict:
    """
    Historical hit-rate by grade across every settled pick (or scoped to a
    sport via ?sport=MLB). Pushes are excluded from denominators; void bets
    are excluded entirely.
    """
    conn = _open_db()
    try:
        table = RealizationTracker.hit_rate_by_grade(conn, sport=sport)
    finally:
        conn.close()
    return {
        "sport": sport,
        "by_grade": table,
    }
