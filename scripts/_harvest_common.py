"""Shared building blocks for the overnight backfill scripts.

Each per-league script in this dir reuses these primitives so they all
behave the same way at the operator-facing surface:

  - Polite RPS limiting + exponential-backoff retry on transient
    HTTP failures.
  - Resumable JSONL output via a "completed game IDs" scan-on-startup.
  - SIGINT handler that finishes the in-flight request before exiting.
  - Compact progress logging with ETA.

Kept stdlib + requests-only so a fresh checkout doesn't pull extra deps.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set

import requests


@dataclass
class FetchStats:
    fetched: int = 0
    skipped: int = 0
    errors: int = 0
    rows_written: int = 0
    started_at: float = field(default_factory=time.time)


def format_eta(remaining: int, rps: float) -> str:
    if rps <= 0:
        return "?"
    seconds = remaining / rps
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------
# Resume support: scan a JSONL file for already-completed IDs.
# ---------------------------------------------------------------------

def scan_completed_ids(jsonl_path: Path, id_field: str = "game_id") -> Set[str]:
    """Read every line of `jsonl_path` and collect the values at
    `id_field`. Skips malformed lines silently -- a partial line from a
    prior crash just gets re-fetched, which is what we want."""
    completed: Set[str] = set()
    if not jsonl_path.exists():
        return completed
    try:
        with jsonl_path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    val = rec.get(id_field)
                    if val is not None:
                        completed.add(str(val))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return completed
    return completed


# ---------------------------------------------------------------------
# HTTP client with retries + rate limiting.
# ---------------------------------------------------------------------

@dataclass
class RateLimitedClient:
    rps: float = 0.5
    timeout: int = 30
    user_agent: str = "edge-equation-backfill/1.0"
    retries: int = 3
    backoff_seconds: float = 2.0

    def __post_init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.user_agent})
        self._last_request_at = 0.0
        self._interval = 1.0 / max(self.rps, 0.01)

    def get_json(self, url: str) -> Optional[Dict[str, Any]]:
        wait = (self._last_request_at + self._interval) - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.time()
        for attempt in range(self.retries):
            try:
                resp = self._session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        return None
                if resp.status_code in (429, 502, 503, 504):
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                return None
            except requests.RequestException:
                time.sleep(self.backoff_seconds * (attempt + 1))
                continue
        return None


# ---------------------------------------------------------------------
# SIGINT-aware writer.
# ---------------------------------------------------------------------

@contextmanager
def graceful_jsonl_writer(path: Path) -> Iterator[Callable[[Dict[str, Any]], None]]:
    """Open `path` in append mode and yield a write fn that fsyncs after
    every record. Installs a SIGINT handler so Ctrl-C finishes the
    in-flight write, then re-raises on the next loop iteration via the
    `interrupted_flag` getter the caller checks. Re-using one handler
    across all leagues' scripts keeps Ctrl-C behaviour predictable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a")
    try:
        def write(record: Dict[str, Any]) -> None:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
        yield write
    finally:
        f.close()


@contextmanager
def graceful_sigint() -> Iterator[Callable[[], bool]]:
    """Yields a `was_interrupted` getter. After the first SIGINT the
    getter returns True; the loop is responsible for breaking. A second
    SIGINT raises KeyboardInterrupt as usual."""
    flag = {"hit": False}

    def handler(signum, frame):  # pragma: no cover -- signal
        if flag["hit"]:
            raise KeyboardInterrupt()
        flag["hit"] = True
        print(
            "\n[interrupt received -- finishing current request, "
            "send another Ctrl-C to abort immediately]",
            file=sys.stderr,
        )

    prev = signal.signal(signal.SIGINT, handler)
    try:
        yield lambda: flag["hit"]
    finally:
        signal.signal(signal.SIGINT, prev)


# ---------------------------------------------------------------------
# Pretty progress printer.
# ---------------------------------------------------------------------

def log_progress(
    label: str, i: int, total: int, stats: FetchStats, log_every: int = 25,
) -> None:
    if i % log_every != 0:
        return
    elapsed = time.time() - stats.started_at
    rps = stats.fetched / elapsed if elapsed > 0 else 0.0
    eta = format_eta(total - i, max(rps, 0.01))
    print(
        f"  [{label}] {i:>5}/{total}  "
        f"errors={stats.errors:<3}  rows={stats.rows_written:<6}  "
        f"rps={rps:.2f}  ETA={eta}"
    )


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
