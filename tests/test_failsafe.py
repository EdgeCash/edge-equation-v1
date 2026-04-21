from datetime import datetime
import pytest

from edge_equation.publishing.failsafe import (
    FileFailsafe,
    SmtpFailsafe,
    CompositeFailsafe,
    default_failsafe,
    ENV_FAILSAFE_DIR,
    ENV_SMTP_HOST,
    ENV_SMTP_FROM,
    ENV_SMTP_TO,
)


# ----------------------------------------------------------- FileFailsafe


def test_file_failsafe_writes_timestamped_file(tmp_path):
    fs = FileFailsafe(directory=str(tmp_path))
    now = datetime(2026, 4, 20, 14, 30, 15)
    detail = fs.deliver(subject="Test subject", body="Test body", target="x", now=now)
    assert "file=" in detail
    assert "x-20260420-143015.txt" in detail
    path = tmp_path / "x-20260420-143015.txt"
    assert path.exists()
    contents = path.read_text(encoding="utf-8")
    assert "SUBJECT: Test subject" in contents
    assert "Test body" in contents


def test_file_failsafe_creates_directory_if_missing(tmp_path):
    nested = tmp_path / "nested" / "dir"
    fs = FileFailsafe(directory=str(nested))
    fs.deliver(subject="s", body="b")
    assert nested.exists()


def test_file_failsafe_default_directory_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_FAILSAFE_DIR, str(tmp_path))
    fs = FileFailsafe()
    assert fs.directory == str(tmp_path)


def test_file_failsafe_isolates_targets(tmp_path):
    fs = FileFailsafe(directory=str(tmp_path))
    now = datetime(2026, 4, 20, 10, 0, 0)
    fs.deliver(subject="s", body="b", target="x", now=now)
    fs.deliver(subject="s", body="b", target="discord", now=now)
    assert (tmp_path / "x-20260420-100000.txt").exists()
    assert (tmp_path / "discord-20260420-100000.txt").exists()


# ----------------------------------------------------------- SmtpFailsafe


class _FakeSmtp:
    """Minimal fake for smtplib.SMTP context manager usage."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = False
        self.sent_messages = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = True
        self.user = user
        self.password = password

    def send_message(self, msg):
        self.sent_messages.append(msg)


def _smtp_factory(captured):
    def factory(host, port):
        smtp = _FakeSmtp(host, port)
        captured.append(smtp)
        return smtp
    return factory


def test_smtp_requires_host():
    with pytest.raises(ValueError, match="host"):
        SmtpFailsafe(host=None, from_addr="a@b", to_addr="c@d")


def test_smtp_requires_from_and_to():
    with pytest.raises(ValueError, match="from_addr"):
        SmtpFailsafe(host="smtp.x", from_addr=None, to_addr="c@d")


def test_smtp_delivers_message_with_login(monkeypatch):
    captured = []
    fs = SmtpFailsafe(
        host="smtp.test", port=587, user="u", password="p",
        from_addr="me@e.com", to_addr="ops@e.com",
        smtp_factory=_smtp_factory(captured),
    )
    detail = fs.deliver(subject="Fail", body="Could not post.", target="x")
    assert detail == "smtp=ops@e.com"
    assert len(captured) == 1
    smtp = captured[0]
    assert smtp.started_tls is True
    assert smtp.logged_in is True
    assert smtp.user == "u"
    msg = smtp.sent_messages[0]
    assert msg["Subject"] == "Fail"
    assert msg["From"] == "me@e.com"
    assert msg["To"] == "ops@e.com"
    assert "Could not post." in msg.get_content()


def test_smtp_skips_login_when_no_credentials():
    captured = []
    fs = SmtpFailsafe(
        host="smtp.test", port=587, user=None, password=None,
        from_addr="me@e.com", to_addr="ops@e.com",
        smtp_factory=_smtp_factory(captured),
    )
    fs.deliver(subject="s", body="b")
    assert captured[0].logged_in is False


def test_smtp_loads_config_from_env(monkeypatch):
    monkeypatch.setenv(ENV_SMTP_HOST, "smtp.env")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv(ENV_SMTP_FROM, "e@e")
    monkeypatch.setenv(ENV_SMTP_TO, "o@o")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    captured = []
    fs = SmtpFailsafe(smtp_factory=_smtp_factory(captured))
    assert fs.host == "smtp.env"
    assert fs.port == 465
    assert fs.from_addr == "e@e"
    assert fs.to_addr == "o@o"


# --------------------------------------------------------- CompositeFailsafe


def test_composite_runs_every_handler_in_order(tmp_path):
    calls = []

    class Recording:
        def __init__(self, name):
            self.name = name

        def deliver(self, subject, body, target="x", now=None):
            calls.append(self.name)
            return f"ok-{self.name}"

    cf = CompositeFailsafe([Recording("a"), Recording("b")])
    detail = cf.deliver(subject="s", body="b")
    assert calls == ["a", "b"]
    assert "Recording:ok-a" in detail
    assert "Recording:ok-b" in detail


def test_composite_one_handler_failure_does_not_abort_others():
    successes = []

    class Boom:
        def deliver(self, subject, body, target="x", now=None):
            raise RuntimeError("kaboom")

    class Works:
        def deliver(self, subject, body, target="x", now=None):
            successes.append(1)
            return "ok"

    cf = CompositeFailsafe([Boom(), Works()])
    detail = cf.deliver(subject="s", body="b")
    assert successes == [1]
    assert "FAILED" in detail
    assert "kaboom" in detail


def test_composite_empty_returns_none_string():
    cf = CompositeFailsafe([])
    assert cf.deliver(subject="s", body="b") == "none"


# ---------------------------------------------------------- default_failsafe


def test_default_failsafe_always_includes_file_handler(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_SMTP_HOST, raising=False)
    monkeypatch.delenv(ENV_SMTP_FROM, raising=False)
    monkeypatch.delenv(ENV_SMTP_TO, raising=False)
    fs = default_failsafe(failsafe_dir=str(tmp_path))
    assert fs is not None
    names = [type(h).__name__ for h in fs.handlers]
    assert "FileFailsafe" in names
    assert "SmtpFailsafe" not in names


def test_default_failsafe_adds_smtp_when_env_configured(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_SMTP_HOST, "smtp.env")
    monkeypatch.setenv(ENV_SMTP_FROM, "e@e")
    monkeypatch.setenv(ENV_SMTP_TO, "o@o")
    fs = default_failsafe(failsafe_dir=str(tmp_path))
    names = [type(h).__name__ for h in fs.handlers]
    assert "FileFailsafe" in names
    assert "SmtpFailsafe" in names


def test_default_failsafe_delivers_to_both_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_SMTP_HOST, "smtp.env")
    monkeypatch.setenv(ENV_SMTP_FROM, "e@e")
    monkeypatch.setenv(ENV_SMTP_TO, "o@o")
    # Replace SmtpFailsafe._factory by patching the class attr wouldn't work
    # here, so we rely on the SMTP handler raising (no real server) and
    # assert the file handler still writes.
    fs = default_failsafe(failsafe_dir=str(tmp_path))
    detail = fs.deliver(subject="s", body="b", now=datetime(2026, 4, 20, 12, 0, 0))
    assert "FileFailsafe:" in detail
    assert "file=" in detail
    # SMTP part is FAILED because the real host won't resolve
    assert "SmtpFailsafe:" in detail
    assert (tmp_path / "x-20260420-120000.txt").exists()
