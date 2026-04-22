"""
--email-preview wiring:
  - Email body is the exact X post text (byte-identical to format_x_text).
  - Subject is prefixed with [X-PREVIEW].
  - --email-preview forces --publish --no-dry-run.
  - X / Discord publishers are never instantiated.
  - The card still passes the compliance gate (public-mode defaults).
"""
import json
from unittest.mock import patch

import pytest

from edge_equation.__main__ import build_parser, main
from edge_equation.publishing.email_publisher import EmailPublisher
from edge_equation.publishing.x_formatter import format_card as format_x_text


class _FakeSmtp:
    """Collect sent messages without touching the network."""
    sent = []

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): pass
    def login(self, u, p): pass
    def send_message(self, msg):
        type(self).sent.append(msg)


def _run(argv, capsys):
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap


def _isolate_email(monkeypatch, tmp_path):
    _FakeSmtp.sent = []
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "bot@edge.com")
    monkeypatch.setenv("EMAIL_TO", "ops@edge.com")
    for v in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
              "X_ACCESS_TOKEN_SECRET", "DISCORD_WEBHOOK_URL",
              "THE_ODDS_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def test_email_preview_flag_parses():
    parser = build_parser()
    args = parser.parse_args(["daily", "--email-preview"])
    assert args.email_preview is True


def test_email_preview_default_off():
    parser = build_parser()
    args = parser.parse_args(["daily"])
    assert args.email_preview is False


def test_email_preview_sends_to_smtp_only(tmp_path, monkeypatch, capsys):
    _isolate_email(monkeypatch, tmp_path)
    db_path = str(tmp_path / "preview.db")

    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        code, cap = _run([
            "daily", "--db", db_path, "--leagues", "MLB",
            "--prefer-mock", "--email-preview",
        ], capsys)

    assert code == 0, cap.err
    payload = json.loads(cap.out)
    # Exactly one publish_result (email only)
    assert len(payload["publish_results"]) == 1
    assert payload["publish_results"][0]["target"] == "email"
    # One message sent via SMTP
    assert len(_FakeSmtp.sent) == 1
    msg = _FakeSmtp.sent[0]
    assert "[X-PREVIEW]" in msg["Subject"]
    assert "daily_edge" in msg["Subject"]


def test_email_preview_body_matches_x_formatter(tmp_path, monkeypatch, capsys):
    _isolate_email(monkeypatch, tmp_path)
    db_path = str(tmp_path / "preview2.db")

    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        _run([
            "daily", "--db", db_path, "--leagues", "MLB",
            "--prefer-mock", "--email-preview",
        ], capsys)

    assert len(_FakeSmtp.sent) == 1
    body = _FakeSmtp.sent[0].get_content()
    # Brand-exact hallmarks must survive end-to-end.
    assert "DAILY EDGE" in body
    assert "Season Ledger:" in body
    assert "#FactsNotFeelings" in body
    assert "#EdgeEquation" in body
    # And no X-only emoji section/divider leaks through (we use the
    # public-mode block-renderer, not the divider layout).
    assert "━━━" not in body


def test_email_preview_forces_publish_regardless_of_dry_run(tmp_path, monkeypatch, capsys):
    _isolate_email(monkeypatch, tmp_path)
    db_path = str(tmp_path / "preview3.db")

    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        # User passes --dry-run explicitly; --email-preview overrides it.
        code, _ = _run([
            "daily", "--db", db_path, "--leagues", "MLB",
            "--prefer-mock", "--email-preview", "--dry-run", "--no-publish",
        ], capsys)
    assert code == 0
    assert len(_FakeSmtp.sent) == 1


def test_email_preview_publisher_uses_x_formatter():
    pub = EmailPublisher(
        host="smtp.test", from_address="a@b", to_address="c@d",
        body_formatter=format_x_text,
        subject_prefix="[X-PREVIEW]",
        failsafe=False,
        smtp_factory=lambda h, p: _FakeSmtp(h, p),
    )
    _FakeSmtp.sent = []
    card = {
        "card_type": "daily_edge",
        "headline": "Daily Edge",
        "subhead": "x",
        "picks": [],
        "summary": {},
        "tagline": "Facts. Not Feelings.",
        "generated_at": "2026-04-22T11:00:00",
    }
    result = pub.publish_card(card)
    assert result.success is True
    body = _FakeSmtp.sent[0].get_content().rstrip("\n")
    assert body == format_x_text(card)
