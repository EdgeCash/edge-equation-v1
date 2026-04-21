import pytest

from edge_equation.publishing.email_publisher import (
    EmailPublisher,
    ENV_SMTP_HOST,
    ENV_SMTP_PORT,
    ENV_SMTP_USER,
    ENV_SMTP_PASSWORD,
    ENV_SMTP_FROM,
    ENV_EMAIL_TO,
    ENV_SMTP_TO,
)


def _card():
    return {
        "card_type": "daily_edge",
        "headline": "Daily Edge",
        "subhead": "Today's plays.",
        "picks": [
            {"market_type": "ML", "selection": "BOS", "grade": "A", "edge": "0.049167",
             "kelly": "0.0324", "fair_prob": "0.618133"},
        ],
        "tagline": "Facts. Not Feelings.",
        "generated_at": "2026-04-20T09:00:00",
    }


def _creds():
    return dict(
        host="smtp.test", port=587, user="u", password="p",
        from_address="bot@e.com", to_address="dist@e.com",
    )


class _FakeSmtp:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = False
        self.sent = []

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): self.started_tls = True
    def login(self, u, p):
        self.logged_in = True
        self.user = u
        self.password = p
    def send_message(self, msg):
        self.sent.append(msg)


def _factory(captured):
    def factory(host, port):
        smtp = _FakeSmtp(host, port)
        captured.append(smtp)
        return smtp
    return factory


def _pub(**overrides):
    captured = []
    kwargs = {**_creds(), "failsafe": False, "smtp_factory": _factory(captured)}
    kwargs.update(overrides)
    return EmailPublisher(**kwargs), captured


# -------------------------------------------------------------------- dry-run


def test_dry_run_success():
    pub, captured = _pub()
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "email"
    assert result.message_id == "dry-run"
    # No SMTP connection made during dry_run
    assert captured == []


def test_dry_run_without_config_still_succeeds():
    pub = EmailPublisher(failsafe=False)
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True


# ------------------------------------------------------ subject / body shape


def test_subject_includes_card_type_and_date():
    pub, _ = _pub()
    subject = pub.build_subject(_card())
    assert "Edge Equation" in subject
    assert "daily_edge" in subject
    assert "2026-04-20" in subject


def test_body_contains_headline_tagline_and_pick():
    pub, _ = _pub()
    body = pub.build_body(_card())
    assert "Daily Edge" in body
    assert "Facts. Not Feelings." in body
    assert "BOS" in body
    assert "Grade: A" in body


# ----------------------------------------------------------------- config


def test_config_loaded_from_env(monkeypatch):
    monkeypatch.setenv(ENV_SMTP_HOST, "smtp.env")
    monkeypatch.setenv(ENV_SMTP_PORT, "465")
    monkeypatch.setenv(ENV_SMTP_USER, "u")
    monkeypatch.setenv(ENV_SMTP_PASSWORD, "p")
    monkeypatch.setenv(ENV_SMTP_FROM, "e@e")
    monkeypatch.setenv(ENV_EMAIL_TO, "list@e")
    pub = EmailPublisher(failsafe=False)
    assert pub.host == "smtp.env"
    assert pub.port == 465
    assert pub.from_address == "e@e"
    assert pub.to_address == "list@e"


def test_to_address_falls_back_to_smtp_to(monkeypatch):
    monkeypatch.delenv(ENV_EMAIL_TO, raising=False)
    monkeypatch.setenv(ENV_SMTP_TO, "fallback@e")
    pub = EmailPublisher(host="smtp", from_address="e@e", failsafe=False)
    assert pub.to_address == "fallback@e"


def test_explicit_kwargs_override_env(monkeypatch):
    monkeypatch.setenv(ENV_SMTP_HOST, "env.smtp")
    pub = EmailPublisher(host="kwarg.smtp", failsafe=False, from_address="e@e", to_address="t@e")
    assert pub.host == "kwarg.smtp"


def test_missing_host_returns_failure_non_dry_run():
    pub = EmailPublisher(from_address="e@e", to_address="t@e", failsafe=False)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert "missing credentials" in (result.error or "")
    assert "SMTP_HOST" in (result.error or "")


def test_missing_to_address_returns_failure(monkeypatch):
    monkeypatch.delenv(ENV_EMAIL_TO, raising=False)
    monkeypatch.delenv(ENV_SMTP_TO, raising=False)
    pub = EmailPublisher(host="smtp", from_address="e@e", failsafe=False)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert "EMAIL_TO" in (result.error or "") or "SMTP_TO" in (result.error or "")


def test_custom_from_address():
    pub = EmailPublisher(from_address="custom@example.com", failsafe=False)
    assert pub.from_address == "custom@example.com"


# ------------------------------------------------- real SMTP (fake factory)


def test_non_dry_run_connects_to_smtp():
    pub, captured = _pub()
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.message_id == "email-to-dist@e.com"
    assert len(captured) == 1
    smtp = captured[0]
    assert smtp.host == "smtp.test"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.logged_in is True


def test_non_dry_run_skips_login_without_credentials():
    pub, captured = _pub(user=None, password=None)
    pub.publish_card(_card(), dry_run=False)
    assert captured[0].logged_in is False


def test_non_dry_run_sends_message_with_headers_and_body():
    pub, captured = _pub()
    pub.publish_card(_card(), dry_run=False)
    msg = captured[0].sent[0]
    assert msg["From"] == "bot@e.com"
    assert msg["To"] == "dist@e.com"
    assert "Edge Equation" in msg["Subject"]
    assert "Daily Edge" in msg.get_content()


def test_publish_card_never_raises_on_smtp_error():
    class BoomSmtp:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, msg): raise ConnectionResetError("smtp down")

    pub = EmailPublisher(
        **_creds(), failsafe=False,
        smtp_factory=lambda h, p: BoomSmtp(h, p),
    )
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert "smtp down" in (result.error or "")


# ----------------------------------------------------- failsafe integration


class _StubFailsafe:
    def __init__(self):
        self.calls = []

    def deliver(self, subject, body, target="email", now=None):
        self.calls.append({"subject": subject, "body": body, "target": target})
        return f"stub:{len(self.calls)}"


def test_failsafe_fires_on_missing_config():
    fs = _StubFailsafe()
    pub = EmailPublisher(failsafe=fs)  # no config
    result = pub.publish_card(_card(), dry_run=False)
    assert result.failsafe_triggered is True
    assert "Edge Equation" in fs.calls[0]["body"]


def test_failsafe_fires_on_smtp_error():
    fs = _StubFailsafe()
    class BoomSmtp:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, msg): raise RuntimeError("boom")
    pub = EmailPublisher(
        **_creds(), failsafe=fs,
        smtp_factory=lambda h, p: BoomSmtp(h, p),
    )
    result = pub.publish_card(_card(), dry_run=False)
    assert result.failsafe_triggered is True
    assert len(fs.calls) == 1


def test_failsafe_not_fired_on_success():
    fs = _StubFailsafe()
    captured = []
    pub = EmailPublisher(
        **_creds(), failsafe=fs,
        smtp_factory=_factory(captured),
    )
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert fs.calls == []


def test_failsafe_disabled_with_false():
    pub = EmailPublisher(failsafe=False)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.failsafe_triggered is False


def test_failsafe_after_failure_no_retry():
    fs = _StubFailsafe()
    attempts = {"count": 0}
    class CountingSmtp:
        def __init__(self, host, port): attempts["count"] += 1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, msg): raise RuntimeError("down")
    pub = EmailPublisher(
        **_creds(), failsafe=fs,
        smtp_factory=lambda h, p: CountingSmtp(h, p),
    )
    pub.publish_card(_card(), dry_run=False)
    assert attempts["count"] == 1
