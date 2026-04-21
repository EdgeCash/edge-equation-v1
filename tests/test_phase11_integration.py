"""
Phase 11 end-to-end integration:

1. All three real publishers (X, Discord, Email) successfully post a card.
2. All three publishers independently fall through to the failsafe when their
   primary path fails.
3. A single card can be fanned out to all three via the publish_runner.
4. Failsafe isolation: a failure on one publisher does not contaminate the
   others.
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import pytest
import httpx

from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.pick_schema import Line
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.publishing.x_publisher import XPublisher
from edge_equation.publishing.discord_publisher import DiscordPublisher
from edge_equation.publishing.email_publisher import EmailPublisher
from edge_equation.publishing.failsafe import FileFailsafe


DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1/test_token"


def _slate_card():
    bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-132))
    return PostingFormatter.build_card(
        card_type="daily_edge",
        picks=[pick],
        generated_at="2026-04-20T09:00:00",
    )


def _x_success_client():
    def handler(request):
        return httpx.Response(200, json={"data": {"id": "1831234567890", "text": "ok"}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _discord_success_client():
    def handler(request):
        return httpx.Response(200, json={"id": "9988776655"})
    return httpx.Client(transport=httpx.MockTransport(handler))


class _FakeSmtp:
    def __init__(self, host, port):
        self.sent = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): pass
    def login(self, u, p): pass
    def send_message(self, msg): self.sent.append(msg)


# ------------------------------------------------------ happy paths


def test_all_three_publishers_succeed(tmp_path):
    card = _slate_card()

    x = XPublisher(
        api_key="CK", api_secret="CS", access_token="AT", access_token_secret="ATS",
        http_client=_x_success_client(), failsafe=False,
    )
    d = DiscordPublisher(
        webhook_url=DISCORD_WEBHOOK,
        http_client=_discord_success_client(),
        failsafe=False,
    )
    e = EmailPublisher(
        host="smtp.test", from_address="bot@e.com", to_address="dist@e.com",
        smtp_factory=lambda h, p: _FakeSmtp(h, p), failsafe=False,
    )

    rx = x.publish_card(card, dry_run=False, nonce="N", timestamp="T")
    rd = d.publish_card(card, dry_run=False)
    re_ = e.publish_card(card, dry_run=False)

    assert rx.success and rx.message_id == "x-1831234567890"
    assert rd.success and rd.message_id == "discord-9988776655"
    assert re_.success and re_.message_id.startswith("email-to-")
    for r in (rx, rd, re_):
        assert r.failsafe_triggered is False


# --------------------------------------------------- all three failsafes


def test_all_three_publishers_fail_and_route_to_failsafe(tmp_path):
    card = _slate_card()
    fs = FileFailsafe(directory=str(tmp_path))

    def _500(request):
        return httpx.Response(500, json={"error": "server"})

    class BoomSmtp:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, msg): raise RuntimeError("smtp offline")

    x = XPublisher(
        api_key="CK", api_secret="CS", access_token="AT", access_token_secret="ATS",
        http_client=httpx.Client(transport=httpx.MockTransport(_500)),
        failsafe=fs,
    )
    d = DiscordPublisher(
        webhook_url=DISCORD_WEBHOOK,
        http_client=httpx.Client(transport=httpx.MockTransport(_500)),
        failsafe=fs,
    )
    e = EmailPublisher(
        host="smtp.test", from_address="bot@e.com", to_address="dist@e.com",
        smtp_factory=lambda h, p: BoomSmtp(h, p), failsafe=fs,
    )

    rx = x.publish_card(card, dry_run=False)
    rd = d.publish_card(card, dry_run=False)
    re_ = e.publish_card(card, dry_run=False)

    for r in (rx, rd, re_):
        assert r.success is False
        assert r.failsafe_triggered is True
        assert "file=" in (r.failsafe_detail or "")

    # One failsafe file per target was written.
    names = sorted(p.name.split("-", 1)[0] for p in tmp_path.iterdir())
    assert names == ["discord", "email", "x"]


# -------------------------------------------------- isolation across targets


def test_x_failure_does_not_affect_discord_or_email(tmp_path):
    card = _slate_card()
    fs = FileFailsafe(directory=str(tmp_path))

    x = XPublisher(failsafe=fs)  # no creds -> fails
    d = DiscordPublisher(webhook_url=DISCORD_WEBHOOK, http_client=_discord_success_client(), failsafe=False)
    e = EmailPublisher(
        host="smtp.test", from_address="bot@e.com", to_address="dist@e.com",
        smtp_factory=lambda h, p: _FakeSmtp(h, p), failsafe=False,
    )

    rx = x.publish_card(card, dry_run=False)
    rd = d.publish_card(card, dry_run=False)
    re_ = e.publish_card(card, dry_run=False)

    assert rx.success is False and rx.failsafe_triggered is True
    assert rd.success is True
    assert re_.success is True


def test_dry_run_across_all_three_never_fires_failsafe(tmp_path):
    card = _slate_card()
    fs = FileFailsafe(directory=str(tmp_path))

    x = XPublisher(failsafe=fs)
    d = DiscordPublisher(failsafe=fs)
    e = EmailPublisher(failsafe=fs)

    for pub in (x, d, e):
        r = pub.publish_card(card, dry_run=True)
        assert r.success is True
        assert r.failsafe_triggered is False
    # Nothing written to the failsafe directory.
    assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------- publish_runner fanout


def test_runner_fanout_with_all_three_dry_run():
    from edge_equation.publishing.publish_runner import publish_card
    card = _slate_card()
    results = publish_card(card, dry_run=True)
    assert len(results) == 3
    assert {r.target for r in results} == {"x", "discord", "email"}
    assert all(r.success and r.message_id == "dry-run" for r in results)


def test_runner_failsafe_records_per_target(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path))
    for v in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
              "DISCORD_WEBHOOK_URL", "SMTP_HOST", "SMTP_FROM", "SMTP_TO", "EMAIL_TO"):
        monkeypatch.delenv(v, raising=False)
    from edge_equation.publishing.publish_runner import publish_card
    card = _slate_card()
    results = publish_card(card, dry_run=False)

    by_target = {r.target: r for r in results}
    for t in ("x", "discord", "email"):
        assert by_target[t].failsafe_triggered is True

    written = sorted(p.name.split("-", 1)[0] for p in tmp_path.iterdir())
    assert written == ["discord", "email", "x"]
