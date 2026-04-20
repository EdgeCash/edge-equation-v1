"""BaseSource protocol. No network calls, no randomness, pure mock data."""
from datetime import datetime
from typing import Protocol


class BaseSource(Protocol):
    def get_raw_games(self, run_datetime: datetime) -> list: ...
    def get_raw_markets(self, run_datetime: datetime) -> list: ...
