"""Premium router.

Endpoints return the full Monte Carlo premium picks. When
`PREMIUM_AUTH_REQUIRED=true` the routes are gated: the caller must have a
valid session cookie AND an active subscription. Left unset, the routes
stay open (the pre-Phase-17 default so existing clients keep working).
"""
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from api.data_source import premium_picks_for_today, premium_pick_to_out_dict
from api.routers.auth import current_user_or_none
from edge_equation.auth.subscriptions import SubscriptionStore
from edge_equation.auth.users import User
from edge_equation.persistence.db import Database
from edge_equation.premium.premium_cards import build_premium_daily_edge_card


router = APIRouter(prefix="/premium", tags=["premium"])


ENV_PREMIUM_AUTH_REQUIRED = "PREMIUM_AUTH_REQUIRED"


def _gate_enabled() -> bool:
    return os.environ.get(ENV_PREMIUM_AUTH_REQUIRED, "").lower() in ("1", "true", "yes")


def require_subscription(request: Request) -> Optional[User]:
    """
    FastAPI dependency: enforces the paywall when PREMIUM_AUTH_REQUIRED is
    enabled. Returns the User when allowed, raises 401/403 otherwise. When
    the gate is off, returns None and lets the request through -- this
    preserves backward compat with the Phase 5/6A tests.
    """
    if not _gate_enabled():
        return None
    user = current_user_or_none(request)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    conn = Database.open(Database.resolve_path(None))
    try:
        Database.migrate(conn)
        if not SubscriptionStore.has_active(conn, user.user_id):
            raise HTTPException(status_code=403, detail="active subscription required")
    finally:
        conn.close()
    return user


@router.get("/picks/today")
def get_premium_picks_today(
    _user: Optional[User] = Depends(require_subscription),
) -> List[dict]:
    premium = premium_picks_for_today()
    return [premium_pick_to_out_dict(pp) for pp in premium]


@router.get("/cards/daily")
def get_premium_card_daily(
    _user: Optional[User] = Depends(require_subscription),
) -> dict:
    premium = premium_picks_for_today()
    card = build_premium_daily_edge_card(premium)
    card.setdefault("generated_at", datetime.now().isoformat())
    return card
