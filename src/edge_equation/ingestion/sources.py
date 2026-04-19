from dataclasses import dataclass
from typing import Any

@dataclass
class IngestionResult:
    source: str
    payload: Any

class IngestionSource:
    """Base class for ingestion sources."""
    name: str = "base"

    async def fetch(self) -> IngestionResult:
        raise NotImplementedError
