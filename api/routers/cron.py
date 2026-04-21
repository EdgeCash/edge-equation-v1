"""
Cron endpoints for Vercel (or any HTTP-triggered scheduler).

Two endpoints, mounted under /cron:
- GET /cron/daily    -> runs the daily-edge pipeline
- GET /cron/evening  -> runs the evening-edge pipeline

Authentication: every request must include Authorization: Bearer <CRON_SECRET>.
CRON_SECRET is a deploy-time env var; when unset the endpoints refuse to run.
Vercel Cron automatically signs scheduled invocations with this header when
you set the secret on the project.

Side effects are deliberate -- these endpoints mutate the DB and can invoke
real publishers. Unlike the general API layer, /cron is not meant to be
called by clients.
"""
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    CARD_TYPE_EVENING,
    DEFAULT_LEAGUES,
    ScheduledRunner,
)
from edge_equation.persistence.db import Database


router = APIRouter(prefix="/cron", tags=["cron"])

ENV_CRON_SECRET = "CRON_SECRET"


def _require_cron_auth(authorization: Optional[str]) -> None:
    """
    Verify the request carries the cron bearer secret. Anything other than an
    exact Bearer-token match on CRON_SECRET is rejected with 401.
    """
    expected = os.environ.get(ENV_CRON_SECRET)
    if not expected:
        raise HTTPException(status_code=503, detail=f"{ENV_CRON_SECRET} not set on server")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    if authorization[len("Bearer "):].strip() != expected:
        raise HTTPException(status_code=401, detail="bad bearer token")


def _run_card(
    card_type: str,
    leagues: Optional[str],
    publish: bool,
    dry_run: bool,
) -> dict:
    leagues_list = (
        [x.strip().upper() for x in leagues.split(",") if x.strip()]
        if leagues else list(DEFAULT_LEAGUES)
    )
    conn = Database.open(Database.resolve_path(None))
    try:
        Database.migrate(conn)
        summary = ScheduledRunner.run(
            card_type=card_type,
            conn=conn,
            run_datetime=datetime.utcnow(),
            leagues=leagues_list,
            publish=publish,
            dry_run=dry_run,
        )
    finally:
        conn.close()
    return summary.to_dict()


@router.get("/daily")
def cron_daily(
    authorization: Optional[str] = Header(default=None),
    leagues: Optional[str] = Query(default=None),
    publish: bool = Query(default=True),
    dry_run: bool = Query(default=False),
) -> dict:
    _require_cron_auth(authorization)
    return _run_card(CARD_TYPE_DAILY, leagues=leagues, publish=publish, dry_run=dry_run)


@router.get("/evening")
def cron_evening(
    authorization: Optional[str] = Header(default=None),
    leagues: Optional[str] = Query(default=None),
    publish: bool = Query(default=True),
    dry_run: bool = Query(default=False),
) -> dict:
    _require_cron_auth(authorization)
    return _run_card(CARD_TYPE_EVENING, leagues=leagues, publish=publish, dry_run=dry_run)
