"""
That K Report -- mailer unit + CLI integration tests.

The mailer takes an injectable smtp_factory so unit tests exercise
the SSL/STARTTLS branching, login flow, and message body without
ever opening a real socket.  CLI integration tests use the same
hook through the `--email` flag on the projections + results
subcommands.
"""
from __future__ import annotations

import smtplib
from contextlib import contextmanager
from typing import List, Optional

import pytest

from edge_equation.that_k.mailer import (
    DEFAULT_RECIPIENT,
    ENV_EMAIL_TO,
    ENV_SMTP_FROM,
    ENV_SMTP_HOST,
    ENV_SMTP_PASSWORD,
    ENV_SMTP_PORT,
    ENV_SMTP_TO,
    ENV_SMTP_USER,
    MailConfig,
    MailError,
    resolve_mail_config,
    send_email,
)


# ------------------------------------------------ fake SMTP transport

class _FakeSmtp:
    """Stand-in for smtplib.SMTP / SMTP_SSL.  Records every call so
    tests can assert connection mode, auth flow, and message body."""

    def __init__(self):
        self.starttls_calls = 0
        self.login_calls: List[tuple] = []
        self.sent_messages: List[object] = []
        self.quit_called = False

    def starttls(self, context=None):
        self.starttls_calls += 1

    def login(self, user, password):
        self.login_calls.append((user, password))

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def quit(self):
        self.quit_called = True


def _factory_returning(fake: _FakeSmtp):
    captured = {}

    def factory(config: MailConfig):
        captured["config"] = config
        return fake
    return factory, captured


def _baseline_env(**overrides):
    env = {
        ENV_SMTP_HOST: "smtp.example.com",
        ENV_SMTP_PORT: "587",
        ENV_SMTP_USER: "u@example.com",
        ENV_SMTP_PASSWORD: "secret",
        ENV_SMTP_FROM: "edge@edgeequation.com",
        ENV_EMAIL_TO: "ProfessorEdgeCash@gmail.com",
    }
    env.update(overrides)
    return env


# ------------------------------------------------ resolve_mail_config

def test_resolve_mail_config_reads_env_correctly():
    cfg = resolve_mail_config(env=_baseline_env())
    assert cfg.host == "smtp.example.com"
    assert cfg.port == 587
    assert cfg.use_ssl is False
    assert cfg.user == "u@example.com"
    assert cfg.password == "secret"
    assert cfg.sender == "edge@edgeequation.com"
    assert cfg.recipient == "ProfessorEdgeCash@gmail.com"


def test_resolve_mail_config_picks_ssl_branch_for_port_465():
    cfg = resolve_mail_config(env=_baseline_env(SMTP_PORT="465"))
    assert cfg.port == 465
    assert cfg.use_ssl is True


def test_resolve_mail_config_falls_back_to_default_recipient():
    env = _baseline_env()
    env.pop(ENV_EMAIL_TO)
    env.pop(ENV_SMTP_TO, None)
    cfg = resolve_mail_config(env=env)
    assert cfg.recipient == DEFAULT_RECIPIENT


def test_resolve_mail_config_falls_back_to_smtp_to_when_email_to_missing():
    env = _baseline_env()
    env.pop(ENV_EMAIL_TO)
    env[ENV_SMTP_TO] = "fallback@example.com"
    cfg = resolve_mail_config(env=env)
    assert cfg.recipient == "fallback@example.com"


def test_resolve_mail_config_recipient_override_wins():
    cfg = resolve_mail_config(
        env=_baseline_env(),
        recipient_override="custom@example.com",
    )
    assert cfg.recipient == "custom@example.com"


def test_resolve_mail_config_missing_host_raises():
    env = _baseline_env()
    env.pop(ENV_SMTP_HOST)
    with pytest.raises(MailError) as ei:
        resolve_mail_config(env=env)
    assert ENV_SMTP_HOST in str(ei.value)


def test_resolve_mail_config_missing_from_raises():
    env = _baseline_env()
    env.pop(ENV_SMTP_FROM)
    with pytest.raises(MailError) as ei:
        resolve_mail_config(env=env)
    assert ENV_SMTP_FROM in str(ei.value)


def test_resolve_mail_config_invalid_port_raises():
    env = _baseline_env(SMTP_PORT="not-a-number")
    with pytest.raises(MailError):
        resolve_mail_config(env=env)


def test_mail_config_to_dict_omits_secrets():
    cfg = resolve_mail_config(env=_baseline_env())
    audit = cfg.to_dict()
    # Only existence flags + non-secret fields appear.
    assert "password" not in audit
    assert audit["password_set"] is True
    assert audit["user_set"] is True
    # The plain secret string must NOT round-trip into the audit blob.
    assert "secret" not in repr(audit)


# ------------------------------------------------ send_email transport flow

def test_send_email_starttls_branch_calls_starttls_then_login_then_send():
    """Default port (587) -> plain SMTP + explicit STARTTLS, then
    login when creds present, then send_message, then quit."""
    fake = _FakeSmtp()
    factory, captured = _factory_returning(fake)
    cfg = send_email(
        subject="Subj",
        body="Body line\nBody line 2",
        env=_baseline_env(),
        smtp_factory=factory,
    )
    assert fake.starttls_calls == 1
    assert fake.login_calls == [("u@example.com", "secret")]
    assert len(fake.sent_messages) == 1
    msg = fake.sent_messages[0]
    assert msg["Subject"] == "Subj"
    assert msg["From"] == "edge@edgeequation.com"
    assert msg["To"] == "ProfessorEdgeCash@gmail.com"
    assert "Body line" in msg.get_content()
    assert fake.quit_called is True
    # Returned config matches what was used.
    assert cfg.recipient == "ProfessorEdgeCash@gmail.com"
    assert captured["config"].port == 587


def test_send_email_ssl_branch_skips_starttls():
    """Port 465 -> SMTP_SSL transport.  STARTTLS is NOT called
    (the connection is already encrypted)."""
    fake = _FakeSmtp()
    factory, _ = _factory_returning(fake)
    send_email(
        subject="S", body="B",
        env=_baseline_env(SMTP_PORT="465"),
        smtp_factory=factory,
    )
    assert fake.starttls_calls == 0
    assert fake.login_calls == [("u@example.com", "secret")]
    assert len(fake.sent_messages) == 1


def test_send_email_skips_login_when_creds_unset():
    fake = _FakeSmtp()
    factory, _ = _factory_returning(fake)
    env = _baseline_env()
    env.pop(ENV_SMTP_USER)
    env.pop(ENV_SMTP_PASSWORD)
    send_email(
        subject="S", body="B",
        env=env, smtp_factory=factory,
    )
    assert fake.login_calls == []
    assert len(fake.sent_messages) == 1


def test_send_email_recipient_override_wins():
    fake = _FakeSmtp()
    factory, _ = _factory_returning(fake)
    send_email(
        subject="S", body="B",
        env=_baseline_env(),
        smtp_factory=factory,
        recipient_override="elsewhere@example.com",
    )
    msg = fake.sent_messages[0]
    assert msg["To"] == "elsewhere@example.com"


def test_send_email_login_failure_raises_mailerror():
    """Login failure must wrap into MailError so the CLI surfaces it
    cleanly (and SMTP plain exception types stay encapsulated)."""

    class _LoginFailFake(_FakeSmtp):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"Bad creds")

    fake = _LoginFailFake()
    factory, _ = _factory_returning(fake)
    with pytest.raises(MailError) as ei:
        send_email(
            subject="S", body="B",
            env=_baseline_env(),
            smtp_factory=factory,
        )
    assert "login" in str(ei.value).lower()
    # quit() still ran (best-effort cleanup).
    assert fake.quit_called is True


def test_send_email_send_failure_raises_mailerror():
    class _SendFailFake(_FakeSmtp):
        def send_message(self, msg):
            raise smtplib.SMTPDataError(550, b"Mailbox full")

    fake = _SendFailFake()
    factory, _ = _factory_returning(fake)
    with pytest.raises(MailError) as ei:
        send_email(
            subject="S", body="B",
            env=_baseline_env(),
            smtp_factory=factory,
        )
    assert "send" in str(ei.value).lower()


# ------------------------------------------------ CLI integration

def _set_env(monkeypatch, **overrides):
    """Apply baseline SMTP env to the test process."""
    env = _baseline_env(**overrides)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_cli_projections_email_flag_invokes_send(monkeypatch, tmp_path):
    """projections --email triggers send_email exactly once with the
    expected subject + non-empty body."""
    from edge_equation.that_k import __main__ as cli_main
    sent: List[dict] = []

    def fake_send_email(*, subject, body, **kwargs):
        sent.append({"subject": subject, "body": body, "kwargs": kwargs})
        return MailConfig(
            host="h", port=587, user=None, password=None,
            sender="from@x", recipient="to@x", use_ssl=False,
        )

    monkeypatch.setattr(cli_main, "send_email", fake_send_email)
    _set_env(monkeypatch)
    rc = cli_main.main([
        "projections", "--sample",
        "--date", "2026-04-23",
        "--out", str(tmp_path / "proj.txt"),
        "--email",
    ])
    assert rc == 0
    assert len(sent) == 1
    assert sent[0]["subject"] == "That K Report — 2026-04-23 (Top Plays)"
    assert "That K Report — 2026-04-23" in sent[0]["body"]
    assert "Tonight's Top Plays" in sent[0]["body"]


def test_cli_results_email_flag_invokes_send(monkeypatch, tmp_path):
    from edge_equation.that_k import __main__ as cli_main
    sent: List[dict] = []

    def fake_send_email(*, subject, body, **kwargs):
        sent.append({"subject": subject, "body": body, "kwargs": kwargs})
        return MailConfig(
            host="h", port=587, user=None, password=None,
            sender="from@x", recipient="to@x", use_ssl=False,
        )

    monkeypatch.setattr(cli_main, "send_email", fake_send_email)
    _set_env(monkeypatch)
    rc = cli_main.main([
        "results", "--sample",
        "--date", "2026-04-22",
        "--no-ledger",
        "--out", str(tmp_path / "res.txt"),
        "--email",
    ])
    assert rc == 0
    assert len(sent) == 1
    assert sent[0]["subject"] == "That K Report — Results · 2026-04-22"
    assert "Yesterday's Top Plays" in sent[0]["body"]


def test_cli_projections_no_email_flag_does_not_send(monkeypatch, tmp_path):
    """Without --email the mailer must NEVER be invoked.  Critical
    safety property -- routine dry runs don't spam the inbox."""
    from edge_equation.that_k import __main__ as cli_main

    def explode(*a, **k):
        raise AssertionError(
            "send_email called without --email -- safety guard breached"
        )

    monkeypatch.setattr(cli_main, "send_email", explode)
    _set_env(monkeypatch)
    rc = cli_main.main([
        "projections", "--sample",
        "--date", "2026-04-23",
        "--out", str(tmp_path / "proj.txt"),
    ])
    assert rc == 0


def test_cli_projections_email_failure_propagates_as_systemexit(monkeypatch, tmp_path):
    """A MailError during the email step should bubble up as a
    non-zero exit so the CI workflow fails loudly instead of
    silently dropping the email."""
    from edge_equation.that_k import __main__ as cli_main

    def boom(*, subject, body, **kwargs):
        raise MailError("smtp died")

    monkeypatch.setattr(cli_main, "send_email", boom)
    _set_env(monkeypatch)
    with pytest.raises(SystemExit):
        cli_main.main([
            "projections", "--sample",
            "--date", "2026-04-23",
            "--out", str(tmp_path / "proj.txt"),
            "--email",
        ])


def test_cli_email_to_override_passes_through(monkeypatch, tmp_path):
    from edge_equation.that_k import __main__ as cli_main
    captured = {}

    def fake_send_email(*, subject, body, recipient_override=None, **kwargs):
        captured["recipient_override"] = recipient_override
        return MailConfig(
            host="h", port=587, user=None, password=None,
            sender="from@x",
            recipient=recipient_override or "default@x",
            use_ssl=False,
        )

    monkeypatch.setattr(cli_main, "send_email", fake_send_email)
    _set_env(monkeypatch)
    rc = cli_main.main([
        "projections", "--sample",
        "--date", "2026-04-23",
        "--out", str(tmp_path / "proj.txt"),
        "--email",
        "--email-to", "custom@example.com",
    ])
    assert rc == 0
    assert captured["recipient_override"] == "custom@example.com"


# ------------------------------------------------ workflow regression

def test_workflow_projections_results_jobs_have_smtp_env_plumbed():
    """Both projections and results jobs MUST carry the SMTP env vars
    so the operator can opt into --email at dispatch time without
    a workflow patch."""
    import re
    from pathlib import Path
    wf = (Path(__file__).resolve().parents[1]
          / ".github" / "workflows" / "that-k-report.yml")
    text = wf.read_text(encoding="utf-8")

    def _job_block(name: str) -> str:
        # Job blocks are at exactly 2-space indent; the next 2-space
        # job header (e.g. "  results:") delimits the block end.
        marker = f"\n  {name}:\n"
        if marker not in text:
            marker = f"  {name}:\n"  # leading-of-file edge case
        start = text.index(marker) + len(marker)
        # Find the next job header (any "\n  WORD:\n" at 2-space indent).
        m = re.search(r"\n  [a-zA-Z_]+:\n", text[start:])
        end = start + m.start() if m else len(text)
        return text[start:end]

    for name in ("projections", "results"):
        block = _job_block(name)
        for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER",
                    "SMTP_PASSWORD", "SMTP_FROM", "EMAIL_TO"):
            assert var in block, (
                f"workflow `{name}` job missing {var} env plumbing"
            )
