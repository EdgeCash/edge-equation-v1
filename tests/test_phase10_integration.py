"""
Phase 10 end-to-end integration:

1. A slate runs through the engine -> posting formatter -> PostingFormatter
   produces a card dict.
2. XPublisher renders the card with the PremiumFormatter and posts it
   (mocked HTTPS transport). On success, returns the tweet id.
3. On failure, the publisher triggers the composite failsafe:
   - writes the intended post to a timestamped file
   - (optionally) emails it via the fake SMTP factory
4. No double-post after the failsafe fires.

Every step is deterministic and free of real network calls.
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
from edge_equation.publishing.x_publisher import XPublisher, TWEETS_ENDPOINT
from edge_equation.publishing.failsafe import (
    CompositeFailsafe,
    FileFailsafe,
    SmtpFailsafe,
    ENV_SMTP_HOST,
    ENV_SMTP_FROM,
    ENV_SMTP_TO,
)


def _slate_card():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )
    pick_ml = BettingEngine.evaluate(bundle, Line(odds=-132))
    bundle2 = FeatureBuilder.build(
        sport="MLB",
        market_type="Total",
        inputs={"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.0},
        universal_features={},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="Over 9.5",
    )
    pick_total = BettingEngine.evaluate(bundle2, Line(odds=-110, number=Decimal('9.5')))
    return PostingFormatter.build_card(
        card_type="daily_edge",
        picks=[pick_ml, pick_total],
        generated_at="2026-04-20T09:00:00",
    )


def _capture_client():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"data": {"id": "1831234567890", "text": "ok"}})

    return httpx.Client(transport=httpx.MockTransport(handler)), captured


def _error_client(status=500):
    def handler(request):
        return httpx.Response(status, json={"title": "err"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_engine_card_posts_through_x_publisher():
    client, captured = _capture_client()
    pub = XPublisher(
        api_key="CK", api_secret="CS", access_token="AT", access_token_secret="ATS",
        http_client=client, failsafe=False,
    )
    card = _slate_card()
    result = pub.publish_card(card, dry_run=False, nonce="N", timestamp="T")
    assert result.success is True
    assert result.message_id == "x-1831234567890"
    # Premium formatting landed in the outgoing body
    assert "DAILY EDGE" in captured["body"]
    assert "BOS" in captured["body"]


def test_dry_run_path_doesnt_hit_api_or_failsafe(tmp_path):
    client, captured = _capture_client()
    fs = FileFailsafe(directory=str(tmp_path))
    pub = XPublisher(
        api_key="CK", api_secret="CS", access_token="AT", access_token_secret="ATS",
        http_client=client, failsafe=fs,
    )
    result = pub.publish_card(_slate_card(), dry_run=True)
    assert result.success is True
    assert result.message_id == "dry-run"
    # no network, no file
    assert captured == {}
    assert list(tmp_path.iterdir()) == []


def test_api_failure_triggers_file_failsafe(tmp_path):
    fs = FileFailsafe(directory=str(tmp_path))
    pub = XPublisher(
        api_key="CK", api_secret="CS", access_token="AT", access_token_secret="ATS",
        http_client=_error_client(500), failsafe=fs,
    )
    result = pub.publish_card(_slate_card(), dry_run=False)
    assert result.success is False
    assert result.failsafe_triggered is True
    assert "file=" in (result.failsafe_detail or "")
    # Verify a failsafe file landed in tmp_path with the premium text
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "DAILY EDGE" in body
    assert "The primary X post failed" in body


def test_missing_creds_triggers_file_failsafe(tmp_path):
    fs = FileFailsafe(directory=str(tmp_path))
    pub = XPublisher(failsafe=fs)  # no credentials
    result = pub.publish_card(_slate_card(), dry_run=False)
    assert result.success is False
    assert result.failsafe_triggered is True
    assert "missing credentials" in (result.error or "")
    files = list(tmp_path.iterdir())
    assert len(files) == 1


def test_composite_failsafe_writes_file_and_calls_smtp(tmp_path):
    smtp_messages = []

    class _FakeSmtp:
        def __init__(self, host, port):
            self.host = host; self.port = port
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, msg): smtp_messages.append(msg)

    file_fs = FileFailsafe(directory=str(tmp_path))
    smtp_fs = SmtpFailsafe(
        host="smtp.test", port=587, user="u", password="p",
        from_addr="bot@e.com", to_addr="ops@e.com",
        smtp_factory=lambda h, p: _FakeSmtp(h, p),
    )
    composite = CompositeFailsafe([file_fs, smtp_fs])

    pub = XPublisher(failsafe=composite)
    result = pub.publish_card(_slate_card(), dry_run=False)
    assert result.failsafe_triggered is True
    assert "FileFailsafe:" in (result.failsafe_detail or "")
    assert "SmtpFailsafe:" in (result.failsafe_detail or "")
    # File written
    assert len(list(tmp_path.iterdir())) == 1
    # Email sent
    assert len(smtp_messages) == 1
    assert "DAILY EDGE" in smtp_messages[0].get_content()


def test_failsafe_fires_exactly_once_no_retry():
    hits = {"count": 0}

    def handler(request):
        hits["count"] += 1
        return httpx.Response(503, json={"title": "Service Unavailable"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    class CountingFailsafe:
        def __init__(self):
            self.n = 0
        def deliver(self, subject, body, target="x", now=None):
            self.n += 1
            return "ok"

    fs = CountingFailsafe()
    pub = XPublisher(
        api_key="CK", api_secret="CS", access_token="AT", access_token_secret="ATS",
        http_client=client, failsafe=fs,
    )
    pub.publish_card(_slate_card(), dry_run=False)
    assert hits["count"] == 1   # one HTTP attempt
    assert fs.n == 1            # one failsafe delivery
    # A second publish_card is the caller's decision -- the class never retries
    # internally.


def test_env_configured_smtp_failsafe_still_writes_file_on_smtp_error(tmp_path, monkeypatch):
    # SMTP env vars point at a bogus host -> real SMTP attempt fails.
    # FileFailsafe still records the post.
    monkeypatch.setenv(ENV_SMTP_HOST, "nonexistent.invalid")
    monkeypatch.setenv(ENV_SMTP_FROM, "bot@e.com")
    monkeypatch.setenv(ENV_SMTP_TO, "ops@e.com")
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path))

    pub = XPublisher()  # no creds; default failsafe = File + SMTP (env)
    result = pub.publish_card(_slate_card(), dry_run=False)
    assert result.failsafe_triggered is True
    # The file part must have succeeded even if SMTP failed
    assert "FileFailsafe:file=" in (result.failsafe_detail or "")
    files = list(tmp_path.iterdir())
    assert len(files) == 1


def test_premium_text_round_trip_through_engine_and_publisher():
    card = _slate_card()
    pub = XPublisher(
        api_key="CK", api_secret="CS", access_token="AT", access_token_secret="ATS",
        failsafe=False,
    )
    text = pub.format_text(card)
    assert "DAILY EDGE" in text
    assert "MLB · ML" in text
    assert "MLB · Total" in text
    assert "9.5 @ -110" in text
    assert "Facts. Not Feelings." in text
