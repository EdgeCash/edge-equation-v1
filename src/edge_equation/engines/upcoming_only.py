"""Upcoming-only failsafe — never publish picks for games already
started or completed.

This module is the centralized filter every publication boundary
consults before a pick / parlay leg lands in today's card. The rule
the audit locked in:

  Include game iff
    - Official status (when known) is one of UPCOMING_STATUSES
    - Scheduled start time is at least `buffer_minutes` after `now`

When status isn't known (some APIs return only commence_time, not
status), we fall through to the time check. When the time isn't
known either, the failsafe is conservative: drop the pick rather
than risk publishing a stale game.

Default buffer: 45 minutes. The cutoff is meant to absorb pre-game
clock drift, lineup-scratch updates, and the engine's own runtime
between projection and commit, while still letting the publish
window cover real first-pitch slates.

Usage:

  from edge_equation.engines.upcoming_only import filter_to_upcoming

  upcoming, skipped = filter_to_upcoming(
      picks,
      get_time=lambda p: p.event_time,
      get_status=lambda p: getattr(p, "status", None),
      log_label="MLB picks",
  )

The returned `skipped` list is the picks the filter dropped, with
a per-row reason string for the workflow log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, List, Optional, Tuple, TypeVar


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — single source of truth for the failsafe.
# ---------------------------------------------------------------------------


DEFAULT_BUFFER_MINUTES: int = 45


# Status strings every supported sched API returns when a game is
# scheduled-but-not-started. Lowercased + stripped at compare time so
# minor casing differences don't leak through.
UPCOMING_STATUSES: frozenset[str] = frozenset({
    "scheduled",
    "pre-game",
    "pre game",
    "preview",
    "warmup",
    "delayed start",
    "delayed",                # rare but possible if first pitch slips
})


# Status strings that ALWAYS mean "do not publish on this game".
# Anything outside both sets falls through to the time-only check.
NEVER_PUBLISH_STATUSES: frozenset[str] = frozenset({
    "in progress",
    "live",
    "final",
    "game over",
    "completed early",
    "postponed",
    "suspended",
    "suspended: rain",
    "cancelled",
    "canceled",                # NCAAF-style spelling
    "forfeit",
})


# ---------------------------------------------------------------------------
# Core check.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailsafeOutcome:
    """One game's failsafe result. ``ok=True`` means publish; ``False``
    means drop, and ``reason`` is the explanation logged to the
    workflow output."""
    ok: bool
    reason: str


def is_upcoming(
    *,
    status: Optional[str] = None,
    scheduled_start: Optional[Any] = None,
    now: Optional[datetime] = None,
    buffer_minutes: int = DEFAULT_BUFFER_MINUTES,
) -> FailsafeOutcome:
    """Return ``FailsafeOutcome(ok, reason)`` for a single game.

    Inputs:

    * ``status`` — official scheduling status string when the API
      surfaces one (e.g. ``"Scheduled"``, ``"In Progress"``,
      ``"Final"``). ``None`` is acceptable — the time check carries
      the load when status isn't known.
    * ``scheduled_start`` — ISO 8601 string OR a tz-aware
      ``datetime`` of first pitch / kickoff / tip-off. ``None`` is
      treated as "unknown — fail safe by dropping" because we cannot
      prove the game is upcoming.
    * ``now`` — defaults to ``datetime.now(timezone.utc)``. Override
      for tests + reproducibility.
    * ``buffer_minutes`` — cushion. The game is dropped if its
      scheduled start is within ``buffer_minutes`` of ``now``.
    """
    now = now or datetime.now(timezone.utc)
    norm_status = (status or "").strip().lower()
    if norm_status in NEVER_PUBLISH_STATUSES:
        return FailsafeOutcome(False, f"status={status!r} (never publish)")

    parsed_start = _parse_dt(scheduled_start)
    if parsed_start is None:
        # No status AND no time — too risky to publish.
        if norm_status in UPCOMING_STATUSES:
            # Status says scheduled but we can't verify time.
            # Allow with a note — better than dropping every legacy
            # row that lacks a parseable time field.
            return FailsafeOutcome(
                True,
                f"status={status!r}, time unknown — admitted on status.",
            )
        return FailsafeOutcome(
            False,
            "scheduled_start not parseable and status not known-good",
        )

    if parsed_start.tzinfo is None:
        # Naive datetime — assume UTC. Pipelines should pass tz-
        # aware values; this is a tolerant fallback.
        parsed_start = parsed_start.replace(tzinfo=timezone.utc)

    cutoff = now + timedelta(minutes=buffer_minutes)
    if parsed_start <= cutoff:
        return FailsafeOutcome(
            False,
            f"start={_iso(parsed_start)} ≤ now+{buffer_minutes}min "
            f"({_iso(cutoff)})",
        )

    return FailsafeOutcome(True, f"start={_iso(parsed_start)} (upcoming)")


# ---------------------------------------------------------------------------
# Bulk filter.
# ---------------------------------------------------------------------------


T = TypeVar("T")


def filter_to_upcoming(
    items: Iterable[T],
    *,
    get_time: Callable[[T], Any],
    get_status: Optional[Callable[[T], Optional[str]]] = None,
    get_label: Optional[Callable[[T], str]] = None,
    now: Optional[datetime] = None,
    buffer_minutes: int = DEFAULT_BUFFER_MINUTES,
    log_prefix: str = "upcoming-only",
) -> Tuple[List[T], List[Tuple[T, str]]]:
    """Filter ``items`` to only games safe to publish.

    Returns ``(upcoming, skipped)``:

    * ``upcoming`` — items the failsafe admitted.
    * ``skipped`` — list of ``(item, reason)`` pairs for the log.

    Skipped items are also logged to stdout via Python logging at
    INFO level so the workflow run shows operators which games were
    dropped. The format mirrors the GitHub Actions ``::notice::``
    convention so the runs UI surfaces them.
    """
    upcoming: list[T] = []
    skipped: list[tuple[T, str]] = []
    for item in items:
        time_value = _safe_call(get_time, item)
        status_value = _safe_call(get_status, item) if get_status else None
        outcome = is_upcoming(
            status=status_value,
            scheduled_start=time_value,
            now=now,
            buffer_minutes=buffer_minutes,
        )
        if outcome.ok:
            upcoming.append(item)
        else:
            skipped.append((item, outcome.reason))
            label = (
                _safe_call(get_label, item) if get_label else None
            ) or str(item)
            print(
                f"::notice::{log_prefix}: dropped {label} — {outcome.reason}",
            )
    if skipped:
        log.info(
            "%s: dropped %d/%d items",
            log_prefix, len(skipped), len(skipped) + len(upcoming),
        )
    return upcoming, skipped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_dt(v: Any) -> Optional[datetime]:
    """Accept str (ISO 8601) or datetime; return UTC datetime or None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        # Treat numeric values as POSIX seconds.
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    # Common variants: "2026-05-06T18:32:00Z", "...+00:00", "..." (naive)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Last-ditch tolerant parse: drop sub-second + try again.
        try:
            return datetime.fromisoformat(s.split(".")[0])
        except ValueError:
            return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _safe_call(fn: Optional[Callable], item: Any) -> Any:
    if fn is None:
        return None
    try:
        return fn(item)
    except Exception:
        return None
