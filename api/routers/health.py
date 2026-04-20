"""Health router."""
from fastapi import APIRouter

from api.schemas.health import Health


router = APIRouter(tags=["health"])

API_VERSION = "v1"


@router.get("/health", response_model=Health)
def get_health() -> Health:
    return Health(status="ok", version=API_VERSION)
