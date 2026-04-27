"""Polite token-bucket rate limiter shared across HTTP clients.

Single global bucket so concurrent scrapers don't accidentally hammer
the same upstream (MLB Stats, Open-Meteo, Baseball Savant). Use the
context-manager form around any outbound request:

    with limiter.acquire():
        resp = httpx.get(...)
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator


class TokenBucket:
    """Simple thread-safe token bucket. `rate` tokens accrue per second."""

    def __init__(self, rate_per_minute: int = 90, burst: int | None = None):
        self.capacity = float(burst if burst is not None else rate_per_minute)
        self.refill_rate = float(rate_per_minute) / 60.0
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        delta = now - self.last_refill
        if delta > 0:
            self.tokens = min(self.capacity, self.tokens + delta * self.refill_rate)
            self.last_refill = now

    @contextmanager
    def acquire(self, tokens: float = 1.0) -> Iterator[None]:
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    break
                wait = (tokens - self.tokens) / self.refill_rate
            time.sleep(max(0.01, wait))
        yield


_GLOBAL: TokenBucket | None = None


def global_limiter(rate_per_minute: int = 90) -> TokenBucket:
    """Return the process-wide bucket, lazily constructed."""
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = TokenBucket(rate_per_minute=rate_per_minute)
    return _GLOBAL
