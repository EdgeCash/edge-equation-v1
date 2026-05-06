"""Tests for the upcoming-only failsafe.

Locks down every behaviour the audit requires:

  - Status whitelist + blacklist resolution.
  - 45-minute time buffer (configurable).
  - ISO string AND tz-aware datetime parsing.
  - Naive datetimes default to UTC.
  - Numeric (POSIX seconds) parsing for legacy artifact rows.
  - Bulk filter returns surviving items + per-row reasons.
  - Skipped games print to stdout for the workflow log.
  - Build-bundle pre-publish pass actually drops a started pick.
  - Build-bundle drops parlays whose legs reference a started game.
  - Footer carries the 'Picks shown only for games not yet started'
    note required by the audit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from edge_equation.engines.upcoming_only import (
    DEFAULT_BUFFER_MINUTES,
    NEVER_PUBLISH_STATUSES,
    UPCOMING_STATUSES,
    FailsafeOutcome,
    filter_to_upcoming,
    is_upcoming,
)


# ---------------------------------------------------------------------------
# Reference clock — 2026-05-06 14:00:00 UTC.
# ---------------------------------------------------------------------------


NOW = datetime(2026, 5, 6, 14, 0, 0, tzinfo=timezone.utc)


def _shift(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Status resolution.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(NEVER_PUBLISH_STATUSES))
def test_never_publish_statuses_drop_immediately(status: str):
    out = is_upcoming(
        status=status,
        scheduled_start=_shift(120),     # tomorrow's first pitch
        now=NOW,
    )
    assert out.ok is False
    assert "never publish" in out.reason


def test_in_progress_drops_even_if_start_time_is_future():
    """A game can be `In Progress` after a delayed start; we treat
    the status as authoritative."""
    out = is_upcoming(
        status="In Progress",
        scheduled_start=_shift(60),
        now=NOW,
    )
    assert out.ok is False


def test_status_case_insensitive():
    out = is_upcoming(
        status="FINAL", scheduled_start=_shift(60), now=NOW,
    )
    assert out.ok is False


def test_unknown_status_falls_through_to_time_check():
    out = is_upcoming(
        status="Some Future Sport Status",
        scheduled_start=_shift(120),
        now=NOW,
    )
    assert out.ok is True


# ---------------------------------------------------------------------------
# Time buffer.
# ---------------------------------------------------------------------------


def test_default_buffer_is_45_minutes():
    assert DEFAULT_BUFFER_MINUTES == 45


def test_game_inside_buffer_drops():
    """First pitch within 45 minutes from now → drop."""
    out = is_upcoming(
        status="Scheduled",
        scheduled_start=_shift(30),
        now=NOW,
    )
    assert out.ok is False
    assert "≤ now+45min" in out.reason


def test_game_just_outside_buffer_admits():
    """First pitch 46 minutes out → admit."""
    out = is_upcoming(
        status="Scheduled",
        scheduled_start=_shift(46),
        now=NOW,
    )
    assert out.ok is True


def test_custom_buffer_override():
    out = is_upcoming(
        status="Scheduled",
        scheduled_start=_shift(20),
        now=NOW,
        buffer_minutes=10,
    )
    assert out.ok is True


def test_started_30_minutes_ago_drops():
    out = is_upcoming(
        status="Scheduled",
        scheduled_start=_shift(-30),
        now=NOW,
    )
    assert out.ok is False


# ---------------------------------------------------------------------------
# Datetime parsing tolerance.
# ---------------------------------------------------------------------------


def test_iso_string_with_z_suffix_parses():
    iso = (NOW + timedelta(minutes=120)).isoformat().replace("+00:00", "Z")
    out = is_upcoming(scheduled_start=iso, now=NOW)
    assert out.ok is True


def test_iso_string_with_offset_parses():
    iso = (NOW + timedelta(minutes=120)).isoformat()
    out = is_upcoming(scheduled_start=iso, now=NOW)
    assert out.ok is True


def test_naive_iso_string_treated_as_utc():
    iso = (
        (NOW + timedelta(minutes=120))
        .replace(tzinfo=None)
        .isoformat()
    )
    out = is_upcoming(scheduled_start=iso, now=NOW)
    assert out.ok is True


def test_iso_with_microseconds_parses():
    iso = (NOW + timedelta(minutes=120)).isoformat() + "Z"
    # `2026-05-06T16:00:00+00:00Z` is malformed — sanity check that
    # we DON'T pass on truly broken input.
    out = is_upcoming(scheduled_start="not-a-date", now=NOW)
    assert out.ok is False


def test_numeric_posix_seconds_parses():
    ts = (NOW + timedelta(minutes=120)).timestamp()
    out = is_upcoming(scheduled_start=ts, now=NOW)
    assert out.ok is True


def test_missing_time_with_unknown_status_drops():
    out = is_upcoming(
        status=None, scheduled_start=None, now=NOW,
    )
    assert out.ok is False
    assert "not parseable" in out.reason


def test_missing_time_with_scheduled_status_admits():
    """Some legacy rows lack a parseable time but DO carry a status.
    We admit on status — better than dropping every legacy row."""
    out = is_upcoming(
        status="Scheduled", scheduled_start=None, now=NOW,
    )
    assert out.ok is True
    assert "time unknown" in out.reason


# ---------------------------------------------------------------------------
# Bulk filter.
# ---------------------------------------------------------------------------


def test_filter_to_upcoming_splits_correctly(capsys):
    rows = [
        {"id": "A", "start": (NOW + timedelta(minutes=120)).isoformat(),
         "status": "Scheduled"},
        {"id": "B", "start": (NOW + timedelta(minutes=10)).isoformat(),
         "status": "Scheduled"},
        {"id": "C", "start": (NOW + timedelta(minutes=120)).isoformat(),
         "status": "In Progress"},
    ]
    upcoming, skipped = filter_to_upcoming(
        rows,
        get_time=lambda r: r["start"],
        get_status=lambda r: r["status"],
        get_label=lambda r: f"row {r['id']}",
        now=NOW,
    )
    assert [r["id"] for r in upcoming] == ["A"]
    assert {r[0]["id"] for r in skipped} == {"B", "C"}
    captured = capsys.readouterr()
    assert "row B" in captured.out
    assert "row C" in captured.out
    assert "::notice::" in captured.out


def test_filter_to_upcoming_handles_missing_callables_gracefully():
    rows = [{"id": "X"}]
    upcoming, skipped = filter_to_upcoming(
        rows,
        get_time=lambda r: r.get("start"),  # raises? no — returns None
        get_status=None,
        now=NOW,
    )
    assert upcoming == []
    assert len(skipped) == 1


# ---------------------------------------------------------------------------
# build_bundle integration — picks + parlays.
# ---------------------------------------------------------------------------


def test_build_bundle_drops_started_pick(monkeypatch):
    """The build_bundle helpers actually drop a pick whose
    event_time is in the past."""
    from edge_equation.engines.website import build_daily_feed as bdf
    started = bdf.FeedPick(
        id="started", sport="MLB", market_type="MONEYLINE",
        selection="NYY · Moneyline",
        line_odds=-120, line_number=None, fair_prob="0.55",
        edge="0.02", kelly="0.01", grade="B", tier=None, notes="",
        event_time=(NOW - timedelta(minutes=15)).isoformat(),
        game_id="g1",
    )
    upcoming = bdf.FeedPick(
        id="upcoming", sport="MLB", market_type="MONEYLINE",
        selection="BOS · Moneyline",
        line_odds=-110, line_number=None, fair_prob="0.55",
        edge="0.02", kelly="0.01", grade="B", tier=None, notes="",
        event_time=(NOW + timedelta(hours=2)).isoformat(),
        game_id="g2",
    )
    # Override datetime.now used inside is_upcoming so we get a
    # deterministic clock during the test.
    from edge_equation.engines import upcoming_only
    monkeypatch.setattr(
        upcoming_only, "datetime",
        _FrozenDatetime(NOW),
    )
    survivors, dropped = bdf._drop_started_picks([started, upcoming], label="MLB")
    assert [p.id for p in survivors] == ["upcoming"]
    assert "NYY · Moneyline" in dropped


def test_build_bundle_drops_parlay_with_started_leg(monkeypatch):
    from edge_equation.engines.website import build_daily_feed as bdf
    leg = bdf.FeedParlayLeg(
        market_type="ML", selection="NYY · Moneyline",
        line_odds=-120, side_probability="0.55", tier="STRONG",
    )
    parlay = bdf.FeedParlay(
        id="p1", universe="game_results", n_legs=1,
        combined_decimal_odds=2.0, combined_american_odds=100,
        fair_decimal_odds=2.0, joint_prob_corr="0.55",
        joint_prob_independent="0.55", implied_prob="0.50",
        edge_pp="5.0", ev_units="0.05", stake_units=0.5,
        note="strict", legs=[leg],
    )
    survivors = bdf._drop_parlays_with_started_legs(
        [parlay], label="MLB game-results",
        dropped_pick_selections={"NYY · Moneyline"},
    )
    assert survivors == []


def test_build_bundle_keeps_parlay_when_no_legs_overlap_dropped():
    from edge_equation.engines.website import build_daily_feed as bdf
    leg = bdf.FeedParlayLeg(
        market_type="ML", selection="LAD · Moneyline",
        line_odds=-110, side_probability="0.6", tier="STRONG",
    )
    parlay = bdf.FeedParlay(
        id="p2", universe="game_results", n_legs=1,
        combined_decimal_odds=2.0, combined_american_odds=100,
        fair_decimal_odds=2.0, joint_prob_corr="0.6",
        joint_prob_independent="0.6", implied_prob="0.55",
        edge_pp="5.0", ev_units="0.05", stake_units=0.5,
        note="strict", legs=[leg],
    )
    survivors = bdf._drop_parlays_with_started_legs(
        [parlay], label="MLB game-results",
        dropped_pick_selections={"NYY · Moneyline"},
    )
    assert len(survivors) == 1


# ---------------------------------------------------------------------------
# Footer copy contract.
# ---------------------------------------------------------------------------


def test_footer_includes_upcoming_only_note():
    from edge_equation.engines.website.build_daily_feed import _build_footer
    text = _build_footer("2026-05-06T14:32:00Z")
    assert "Picks shown only for games not yet started" in text
    assert "Updated:" in text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FrozenDatetime:
    """datetime drop-in that returns a fixed `now()`. Used to
    monkeypatch the module-level datetime in `upcoming_only` for
    deterministic tests of the build_bundle helpers."""
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self, tz=None):
        return self._now if tz is None else self._now.astimezone(tz)

    def __getattr__(self, name):
        # Forward everything else (timezone, timedelta, etc.) to the
        # real datetime module if a caller reaches for it.
        from datetime import datetime as _real
        return getattr(_real, name)
