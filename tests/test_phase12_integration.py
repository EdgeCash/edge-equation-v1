"""
Phase 12 end-to-end integration:

The scheduler ties the whole engine together. These tests exercise the full
flow as a scheduler (Vercel cron, GitHub Action, crontab, whatever) would:

  SourceFactory -> ingestion -> normalize -> engine -> picks -> persist
                -> card -> publish -> failsafe-on-failure -> settle outcomes.

Every test either drives the mocks or feeds a CSV so nothing touches real
networks or The Odds API.
"""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import pytest

from edge_equation.engine.realization import SETTLED_LOSS, SETTLED_WIN
from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    CARD_TYPE_EVENING,
    CARD_TYPE_OVERSEAS_EDGE,
    ScheduledRunner,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.realization_store import RealizationStore
from edge_equation.persistence.slate_store import SlateStore
from edge_equation.publishing.base_publisher import PublishResult


RUN_DT = datetime(2026, 4, 20, 9, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


class _Recorder:
    """Minimal publisher spy returning success."""
    def __init__(self, target):
        self.target = target
        self.calls = []
    def publish_card(self, card, dry_run=False):
        self.calls.append({"card": card, "dry_run": dry_run})
        return PublishResult(success=True, target=self.target, message_id=f"{self.target}-1")


# ------------------------------------------------- end-to-end happy path


def test_full_scheduler_flow_persists_and_publishes(conn):
    pubs = [_Recorder("x"), _Recorder("discord"), _Recorder("email")]
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["MLB", "NHL"],
        prefer_mock=True,
        publish=True,
        dry_run=True,
        publishers=pubs,
    )

    # Slate persisted
    slate = SlateStore.get(conn, summary.slate_id)
    assert slate is not None
    assert slate.card_type == CARD_TYPE_DAILY

    # Picks persisted
    picks = PickStore.list_by_slate(conn, summary.slate_id)
    assert len(picks) == summary.n_picks > 0
    sports = {p.sport for p in picks}
    assert "MLB" in sports

    # All three publishers fired exactly once each
    for p in pubs:
        assert len(p.calls) == 1
    assert len(summary.publish_results) == 3
    assert all(r.success for r in summary.publish_results)


# ---------------------------------------------------- CSV source wins over mock


def test_dated_csv_overrides_mock(conn, tmp_path):
    # Manual CSV rows carry market + odds + line but not the engine feature
    # inputs (strength_home, off_env, etc.). Those come from a stats layer
    # that doesn't exist yet. So a CSV-only KBO slate should persist the slate
    # and log the games, but produce 0 engine picks.
    csv_body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,Doosan Bears,,-140\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,LG Twins,,+120\n"
    )
    (tmp_path / "kbo_2026-04-20.csv").write_text(csv_body, encoding="utf-8")

    # Phase 24 slate separation: KBO is overseas-only; swap the card
    # type accordingly so the runner's off-slate filter doesn't drop
    # the whole league out.
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_OVERSEAS_EDGE,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["KBO"],
        csv_dir=str(tmp_path),
        prefer_mock=False,
    )
    assert summary.new_slate is True
    assert summary.n_games == 1
    # The persisted SlateRecord metadata records that the CSV source was used.
    slate = SlateStore.get(conn, summary.slate_id)
    assert slate is not None
    assert slate.metadata.get("csv_dir") == str(tmp_path)


# --------------------------------------------------------- idempotency


def test_rerun_does_not_duplicate_picks_or_republish(conn):
    pubs = [_Recorder("x"), _Recorder("discord"), _Recorder("email")]
    # First run
    first = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
        publish=True, dry_run=True, publishers=pubs,
    )
    assert first.new_slate is True
    n_picks_after_first = len(PickStore.list_by_slate(conn, first.slate_id))
    call_counts_after_first = [len(p.calls) for p in pubs]

    # Second run: same date, same leagues -> same slate_id -> no-op
    second = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
        publish=True, dry_run=True, publishers=pubs,
    )
    assert second.new_slate is False
    assert second.publish_results == ()
    assert len(PickStore.list_by_slate(conn, second.slate_id)) == n_picks_after_first
    # Publishers NOT invoked again
    assert [len(p.calls) for p in pubs] == call_counts_after_first


# -------------------------------------------- settle flow after the slate runs


def test_run_then_settle_updates_realization(conn):
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_EVENING, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    picks = PickStore.list_by_slate(conn, summary.slate_id)
    assert len(picks) > 0

    # Mark the first pick as a win, second as a loss
    p1, p2 = picks[0], picks[1]
    RealizationStore.record_outcome(conn, p1.game_id, p1.market_type, p1.selection, "win")
    RealizationStore.record_outcome(conn, p2.game_id, p2.market_type, p2.selection, "loss")

    settled = ScheduledRunner.settle(conn, slate_id=summary.slate_id)
    assert settled["matched"] == 2
    assert settled["updated"] == 2

    refreshed = PickStore.list_by_slate(conn, summary.slate_id)
    by_id = {p.pick_id: p for p in refreshed}
    assert by_id[p1.pick_id].realization == SETTLED_WIN
    assert by_id[p2.pick_id].realization == SETTLED_LOSS


# --------------------------------------- failure isolation across publishers


def test_publisher_failure_with_failsafe_does_not_affect_other_publishers(conn):
    class _Failing:
        target = "discord"
        def publish_card(self, card, dry_run=False):
            return PublishResult(
                success=False, target="discord", error="500",
                failsafe_triggered=True, failsafe_detail="file=x.txt",
            )

    pubs = [_Recorder("x"), _Failing(), _Recorder("email")]
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
        publish=True, dry_run=True, publishers=pubs,
    )
    by_target = {r.target: r for r in summary.publish_results}
    assert by_target["x"].success is True
    assert by_target["email"].success is True
    assert by_target["discord"].success is False
    assert by_target["discord"].failsafe_triggered is True


# --------------------------------------------------- two slate types coexist


def test_daily_and_evening_on_same_date_have_distinct_slate_ids(conn):
    daily = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    evening = ScheduledRunner.run(
        card_type=CARD_TYPE_EVENING, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    assert daily.slate_id != evening.slate_id
    assert SlateStore.get(conn, daily.slate_id) is not None
    assert SlateStore.get(conn, evening.slate_id) is not None
