"""
--force / force=True behavior: ensure a manual dispatch with a cached
DB actually rebuilds the slate and invokes publishers, while the
scheduled-cron path (force=False) still short-circuits on idempotency.
"""
import json
import logging
from datetime import datetime
from unittest.mock import patch

import pytest

from edge_equation.__main__ import build_parser, main
from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    ScheduledRunner,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateStore
from edge_equation.publishing.base_publisher import PublishResult


RUN_DT = datetime(2026, 4, 22, 11, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


class _Cap:
    def __init__(self):
        self.calls = []
    def publish_card(self, card, dry_run=False):
        self.calls.append(card)
        return PublishResult(success=True, target="x", message_id="1")


# -------------------------------------------- flag parsing


def test_force_flag_default_off():
    parser = build_parser()
    args = parser.parse_args(["daily"])
    assert args.force is False


def test_force_flag_parses_true():
    parser = build_parser()
    args = parser.parse_args(["daily", "--force"])
    assert args.force is True


# -------------------------------------------- short-circuit behavior


def test_default_run_short_circuits_when_slate_exists(conn):
    cap = _Cap()
    first = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], publish=True, dry_run=True,
        prefer_mock=True, publishers=[cap],
    )
    assert first.new_slate is True
    assert cap.calls, "first run should publish"
    cap.calls.clear()

    # Second run with force=False -> should short-circuit, no publishes.
    second = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], publish=True, dry_run=True,
        prefer_mock=True, publishers=[cap],
    )
    assert second.new_slate is False
    assert second.publish_results == ()
    assert cap.calls == []   # publisher NEVER called


def test_force_true_rebuilds_and_publishes_on_second_run(conn):
    cap = _Cap()
    ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], publish=True, dry_run=True,
        prefer_mock=True, publishers=[cap],
    )
    cap.calls.clear()

    # Second run with force=True should delete the prior slate, rebuild,
    # and publish.
    second = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], publish=True, dry_run=True,
        prefer_mock=True, publishers=[cap],
        force=True,
    )
    assert second.new_slate is True
    assert len(cap.calls) == 1
    # The old slate's picks should have been purged before the rebuild
    # wrote new ones. Row count should match the current pick count
    # (not double).
    picks_now = PickStore.list_by_slate(conn, second.slate_id)
    assert len(picks_now) == second.n_picks


def test_force_true_on_fresh_db_behaves_like_default(conn):
    cap = _Cap()
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], publish=True, dry_run=True,
        prefer_mock=True, publishers=[cap],
        force=True,
    )
    assert summary.new_slate is True
    assert len(cap.calls) == 1


def test_short_circuit_logs_clear_message(conn, caplog):
    ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    with caplog.at_level(logging.INFO, logger="edge-equation.runner"):
        ScheduledRunner.run(
            card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
            leagues=["MLB"], prefer_mock=True,
        )
    messages = [r.message for r in caplog.records]
    assert any("already persisted" in m for m in messages)
    assert any("--force" in m for m in messages)


def test_force_path_logs_deletion(conn, caplog):
    ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    with caplog.at_level(logging.INFO, logger="edge-equation.runner"):
        ScheduledRunner.run(
            card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
            leagues=["MLB"], prefer_mock=True, force=True,
        )
    messages = [r.message for r in caplog.records]
    assert any("force=True" in m and "deleting existing slate" in m for m in messages)


# -------------------------------------------- CLI integration


class _FakeSmtp:
    sent = []
    def __init__(self, host, port, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): pass
    def login(self, u, p): pass
    def send_message(self, m): type(self).sent.append(m)


def _isolate(monkeypatch, tmp_path):
    _FakeSmtp.sent = []
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("SMTP_FROM", "bot@edge.com")
    monkeypatch.setenv("EMAIL_TO", "ProfessorEdgeCash@gmail.com")
    for v in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
              "X_ACCESS_TOKEN_SECRET", "DISCORD_WEBHOOK_URL",
              "THE_ODDS_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def test_cli_without_force_silently_skips_second_run(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "x.db")
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        main(["daily", "--db", db_path, "--leagues", "MLB",
              "--prefer-mock", "--email-preview"])
        capsys.readouterr()
        _FakeSmtp.sent.clear()
        # Re-run without --force: idempotency short-circuits, no email.
        main(["daily", "--db", db_path, "--leagues", "MLB",
              "--prefer-mock", "--email-preview"])
    cap = capsys.readouterr()
    payload = json.loads(cap.out)
    assert payload["new_slate"] is False
    assert payload["publish_results"] == []
    assert _FakeSmtp.sent == []


def test_cli_with_force_sends_email_on_second_run(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "xf.db")
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        main(["daily", "--db", db_path, "--leagues", "MLB",
              "--prefer-mock", "--email-preview"])
        capsys.readouterr()
        _FakeSmtp.sent.clear()
        # Re-run WITH --force: rebuild and actually send the email.
        main(["daily", "--db", db_path, "--leagues", "MLB",
              "--prefer-mock", "--email-preview", "--force"])
    cap = capsys.readouterr()
    payload = json.loads(cap.out)
    assert payload["new_slate"] is True
    assert len(_FakeSmtp.sent) == 1
