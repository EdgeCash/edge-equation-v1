"""
EmailPublisher SMTP-transport fixes: port-based SSL selection, audible
STARTTLS failures, structured logging, auth-failure surfacing.
"""
import logging
import smtplib
from unittest.mock import patch

import pytest

from edge_equation.publishing.email_publisher import (
    ENV_EMAIL_TO,
    ENV_SMTP_FROM,
    ENV_SMTP_HOST,
    EmailPublisher,
)
from edge_equation.publishing.failsafe import FileFailsafe


CARD = {
    "card_type": "daily_edge",
    "headline": "Daily Edge",
    "subhead": "x",
    "picks": [],
    "summary": {},
    "tagline": "Facts. Not Feelings.",
    "generated_at": "2026-04-22T11:00:00",
}


class _SentCapture:
    """One fake SMTP class reused across tests via .hits bookkeeping."""
    hits = []

    def __init__(self, host, port, **kwargs):
        self.host = host
        self.port = port
        type(self).hits.append({"class": type(self).__name__, "host": host, "port": port})

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): pass
    def login(self, u, p): pass
    def send_message(self, m): pass


class _FakeSmtp(_SentCapture): pass
class _FakeSmtpSsl(_SentCapture): pass


@pytest.fixture(autouse=True)
def _reset_hits():
    _SentCapture.hits = []


def _basic_publisher(tmp_path, port, **overrides):
    return EmailPublisher(
        host="smtp.test",
        port=port,
        user="user",
        password="pw",
        from_address="bot@e.com",
        to_address="ops@e.com",
        failsafe=FileFailsafe(directory=str(tmp_path)),
        **overrides,
    )


# ---------------------------------------------- SSL / STARTTLS routing


def test_port_465_uses_smtp_ssl(tmp_path):
    """Implicit-SSL port must open SMTP_SSL, not plain SMTP."""
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP_SSL", _FakeSmtpSsl):
        with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
            pub = _basic_publisher(tmp_path, port=465)
            r = pub.publish_card(CARD)
    assert r.success is True
    assert _SentCapture.hits
    assert _SentCapture.hits[0]["class"] == "_FakeSmtpSsl"


def test_port_587_uses_plain_smtp_for_starttls(tmp_path):
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP_SSL", _FakeSmtpSsl):
        with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
            pub = _basic_publisher(tmp_path, port=587)
            r = pub.publish_card(CARD)
    assert r.success is True
    assert _SentCapture.hits[0]["class"] == "_FakeSmtp"


def test_smtp_factory_override_wins_even_at_port_465(tmp_path):
    """Test fakes passed via smtp_factory MUST be honored verbatim so
    they aren't accidentally swapped for SMTP_SSL on port 465."""
    calls = []
    def fake(host, port):
        calls.append((host, port))
        class _S:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def ehlo(self): pass
            def starttls(self): pass
            def login(self, u, p): pass
            def send_message(self, m): pass
        return _S()
    pub = EmailPublisher(
        host="x", port=465,
        user="u", password="p",
        from_address="b@e", to_address="o@e",
        smtp_factory=fake,
        failsafe=FileFailsafe(directory=str(tmp_path)),
    )
    r = pub.publish_card(CARD)
    assert r.success is True
    assert calls == [("x", 465)]


# ---------------------------------------------- STARTTLS failure is audible


def test_starttls_failure_is_logged_not_swallowed(tmp_path, caplog):
    class _TlsBroken:
        def __init__(self, h, p, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): raise smtplib.SMTPException("tls not offered")
        def login(self, u, p): pass
        def send_message(self, m): pass

    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _TlsBroken):
        pub = _basic_publisher(tmp_path, port=587)
        with caplog.at_level(logging.WARNING, logger="edge-equation.email"):
            r = pub.publish_card(CARD)
    assert r.success is True  # send still succeeded over plaintext
    assert any("STARTTLS failed" in rec.message for rec in caplog.records)


# ---------------------------------------------- auth failure surfaces clearly


def test_auth_failure_surfaces_as_error_and_fires_failsafe(tmp_path, caplog):
    class _AuthRejects:
        def __init__(self, h, p, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p):
            raise smtplib.SMTPAuthenticationError(535, b"Bad credentials")
        def send_message(self, m): pass

    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _AuthRejects):
        pub = _basic_publisher(tmp_path, port=587)
        with caplog.at_level(logging.ERROR, logger="edge-equation.email"):
            r = pub.publish_card(CARD)
    assert r.success is False
    assert r.failsafe_triggered is True
    assert "SMTPAuthenticationError" in (r.error or "")
    # Failsafe directory now holds one .txt for the intended post
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert any("SMTP auth failed" in rec.message for rec in caplog.records)
    # The Gmail app-password hint must appear in the ERROR log
    assert any("app password" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------- missing credentials routes to failsafe


def test_missing_host_routes_to_failsafe_with_warning(tmp_path, caplog, monkeypatch):
    # Strip env vars so the constructor's fallback path has no host.
    monkeypatch.delenv(ENV_SMTP_HOST, raising=False)
    monkeypatch.delenv(ENV_SMTP_FROM, raising=False)
    monkeypatch.delenv(ENV_EMAIL_TO, raising=False)
    pub = EmailPublisher(
        host=None,
        from_address="bot@e.com",
        to_address="ops@e.com",
        failsafe=FileFailsafe(directory=str(tmp_path)),
    )
    with caplog.at_level(logging.WARNING, logger="edge-equation.email"):
        r = pub.publish_card(CARD)
    assert r.success is False
    assert r.failsafe_triggered is True
    assert "SMTP_HOST" in (r.error or "")
    assert any("missing credentials" in rec.message for rec in caplog.records)


def test_anonymous_send_emits_explicit_warning(tmp_path, caplog):
    """Operator misconfigurations where SMTP_USER / SMTP_PASSWORD were
    not set must be loudly flagged before the send attempt so the log
    tells you exactly what to fix."""
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        pub = EmailPublisher(
            host="smtp.test", port=587,
            user=None, password=None,   # <-- anon
            from_address="bot@e.com", to_address="ops@e.com",
            failsafe=FileFailsafe(directory=str(tmp_path)),
        )
        with caplog.at_level(logging.WARNING, logger="edge-equation.email"):
            r = pub.publish_card(CARD)
    assert r.success is True
    assert any("anonymous" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------- success log line


def test_success_emits_send_succeeded_log(tmp_path, caplog):
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        pub = _basic_publisher(tmp_path, port=587)
        with caplog.at_level(logging.INFO, logger="edge-equation.email"):
            r = pub.publish_card(CARD)
    assert r.success is True
    assert any("attempting SMTP+STARTTLS" in rec.message for rec in caplog.records)
    assert any("send succeeded" in rec.message for rec in caplog.records)


# ---------------------------------------------- body still carries Season Ledger footer


def test_default_body_carries_season_ledger_footer(tmp_path):
    """The env's public-mode pipeline injects the footer into card[tagline].
    The default body renderer must include it verbatim."""
    card = dict(CARD)
    card["tagline"] = (
        "Facts. Not Feelings.\n"
        "Pure data from our hybrid ensemble model. Facts. Not Feelings. "
        "What you do with it is on you.\n"
        "Season Ledger: 0-0-0 +0.00 units +0.0 ROI | "
        "Bet within your means. Problem? Call 1-800-GAMBLER."
    )
    body = EmailPublisher.build_body(card)
    assert "Season Ledger:" in body
    assert "Call 1-800-GAMBLER" in body
