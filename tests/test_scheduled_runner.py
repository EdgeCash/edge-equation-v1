from datetime import datetime
import pytest

from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    CARD_TYPE_EVENING,
    DEFAULT_LEAGUES,
    RunSummary,
    ScheduledRunner,
    VALID_CARD_TYPES,
    _slate_id_for,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateStore
from edge_equation.publishing.base_publisher import PublishResult


RUN_DT = datetime(2026, 4, 20, 9, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


class _SuccessPublisher:
    def __init__(self, target):
        self.target = target
        self.calls = []
    def publish_card(self, card, dry_run=False):
        self.calls.append({"card": card, "dry_run": dry_run})
        return PublishResult(success=True, target=self.target, message_id=f"{self.target}-1")


class _FailingPublisher:
    def __init__(self, target):
        self.target = target
    def publish_card(self, card, dry_run=False):
        return PublishResult(success=False, target=self.target, error="boom", failsafe_triggered=True, failsafe_detail="file=x")


# ------------------------------------------------------- slate_id_for


def test_slate_id_multi_league():
    assert _slate_id_for("daily_edge", RUN_DT, ["MLB", "NHL"]) == "daily_edge_20260420"


def test_slate_id_single_league():
    assert _slate_id_for("evening_edge", RUN_DT, ["NHL"]) == "evening_edge_20260420_nhl"


# --------------------------------------------------- validation


def test_invalid_card_type_raises(conn):
    with pytest.raises(ValueError, match="card_type"):
        ScheduledRunner.run(
            card_type="garbage",
            conn=conn,
            run_datetime=RUN_DT,
            prefer_mock=True,
        )


def test_empty_leagues_raises(conn):
    with pytest.raises(ValueError, match="at least one league"):
        ScheduledRunner.run(
            card_type=CARD_TYPE_DAILY,
            conn=conn,
            run_datetime=RUN_DT,
            leagues=[],
            prefer_mock=True,
        )


# --------------------------------------------------- happy path


def test_single_league_run_persists_slate_and_picks(conn):
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["MLB"],
        prefer_mock=True,
    )
    assert isinstance(summary, RunSummary)
    assert summary.new_slate is True
    assert summary.n_picks > 0
    assert summary.slate_id == "daily_edge_20260420_mlb"

    stored_slate = SlateStore.get(conn, summary.slate_id)
    assert stored_slate is not None
    assert stored_slate.card_type == CARD_TYPE_DAILY
    stored_picks = PickStore.list_by_slate(conn, summary.slate_id)
    assert len(stored_picks) == summary.n_picks


def test_multi_league_run_merges_slate(conn):
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_EVENING,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["MLB", "NHL"],
        prefer_mock=True,
    )
    assert summary.slate_id == "evening_edge_20260420"
    assert summary.n_picks > 0
    # Picks should include both sports
    picks = PickStore.list_by_slate(conn, summary.slate_id)
    sports = {p.sport for p in picks}
    assert "MLB" in sports


# ---------------------------------------------------- idempotency


def test_second_run_same_slate_id_is_noop(conn):
    first = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    assert first.new_slate is True
    n_picks_after_first = first.n_picks

    second = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    assert second.new_slate is False
    assert second.n_picks == n_picks_after_first
    # DB still contains only one set of picks for that slate
    assert len(PickStore.list_by_slate(conn, first.slate_id)) == n_picks_after_first


def test_second_run_with_publish_is_noop(conn):
    pubs = [_SuccessPublisher("x"), _SuccessPublisher("discord"), _SuccessPublisher("email")]
    ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True, publish=True, publishers=pubs,
    )
    first_call_counts = [len(p.calls) for p in pubs]
    assert all(c == 1 for c in first_call_counts)

    # Re-run: publishers should NOT be invoked again
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True, publish=True, publishers=pubs,
    )
    assert summary.new_slate is False
    assert summary.publish_results == ()
    # No new publisher calls after the idempotent replay
    for p in pubs:
        assert len(p.calls) == 1


# ---------------------------------------------------- publish fanout


def test_publish_true_fires_every_publisher(conn):
    pubs = [_SuccessPublisher("x"), _SuccessPublisher("discord"), _SuccessPublisher("email")]
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True, publish=True, publishers=pubs,
    )
    assert len(summary.publish_results) == 3
    assert {r.target for r in summary.publish_results} == {"x", "discord", "email"}
    assert all(r.success for r in summary.publish_results)


def test_publish_false_skips_publishers(conn):
    pubs = [_SuccessPublisher("x"), _SuccessPublisher("discord")]
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True, publish=False, publishers=pubs,
    )
    assert summary.publish_results == ()
    for p in pubs:
        assert len(p.calls) == 0


def test_dry_run_propagates_to_publishers(conn):
    pubs = [_SuccessPublisher("x")]
    ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True, publish=True, dry_run=True, publishers=pubs,
    )
    assert pubs[0].calls[0]["dry_run"] is True


def test_publisher_failure_with_failsafe_still_returns(conn):
    pubs = [_SuccessPublisher("x"), _FailingPublisher("discord"), _SuccessPublisher("email")]
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True, publish=True, publishers=pubs,
    )
    by_target = {r.target: r for r in summary.publish_results}
    assert by_target["x"].success is True
    assert by_target["discord"].success is False
    assert by_target["discord"].failsafe_triggered is True
    assert by_target["email"].success is True


# --------------------------------------------------------- summary shape


def test_summary_to_dict_roundtrips_publisher_results(conn):
    pubs = [_SuccessPublisher("x")]
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True, publish=True, publishers=pubs,
    )
    d = summary.to_dict()
    assert d["slate_id"] == summary.slate_id
    assert d["publish_results"][0]["target"] == "x"
    assert d["new_slate"] is True


def test_summary_frozen(conn):
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    with pytest.raises(Exception):
        summary.slate_id = "hacked"


# --------------------------------------------------------- settle


def test_settle_empty_db_returns_zero(conn):
    result = ScheduledRunner.settle(conn)
    assert result["matched"] == 0
    assert result["updated"] == 0


# --------------------------------------------------------- defaults


def test_default_leagues_constant_is_reasonable():
    assert "MLB" in DEFAULT_LEAGUES
    assert "KBO" in DEFAULT_LEAGUES
    assert len(DEFAULT_LEAGUES) >= 5


def test_valid_card_types_constant():
    # Phase 21 expands the runner to the full five-window daily cadence.
    assert set(VALID_CARD_TYPES) == {
        "daily_edge", "evening_edge", "the_ledger",
        "spotlight", "overseas_edge",
    }
