"""
FastAPI entrypoint for the Edge Equation API.

Run locally with:
    uvicorn api.main:app --reload
"""
from fastapi import FastAPI

from api.routers import (
    archive,
    auth,
    cards,
    cron,
    health,
    picks,
    premium,
    slate,
    stripe_router,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Edge Equation API",
        version="v1",
        description="Deterministic sports analytics engine. Facts. Not Feelings.",
    )
    app.include_router(health.router)
    app.include_router(picks.router)
    app.include_router(cards.router)
    app.include_router(premium.router)
    app.include_router(slate.router)
    app.include_router(cron.router)
    app.include_router(archive.router)
    app.include_router(auth.router)
    app.include_router(stripe_router.router)
    return app


app = create_app()
