"""Health schema."""
from pydantic import BaseModel


class Health(BaseModel):
    status: str
    version: str
