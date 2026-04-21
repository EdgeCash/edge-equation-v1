"""
Post-failure failsafes.

When a primary publish path (X, Discord, email) fails, the publisher should
NOT retry the post -- doing so after the operator has already been notified
risks a double-post once the network recovers or credentials are fixed. The
failsafes in this module capture the would-be post somewhere the operator can
act on manually.

Two built-in failsafes; both accept a `subject` and `body` string and return a
detail string describing what was done.

- FileFailsafe: writes to data/failsafes/{target}-{YYYYMMDD-HHMMSS}.txt.
  Always available, zero config, deterministic.
- SmtpFailsafe: sends via stdlib smtplib (starttls, login, sendmail). Config
  comes from kwargs or env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
  SMTP_FROM, SMTP_TO. Disabled when SMTP_HOST is not set.

Either failsafe may fail in turn -- the composite handler tries each and
returns a concatenated detail string recording which succeeded.
"""
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional


ENV_SMTP_HOST = "SMTP_HOST"
ENV_SMTP_PORT = "SMTP_PORT"
ENV_SMTP_USER = "SMTP_USER"
ENV_SMTP_PASSWORD = "SMTP_PASSWORD"
ENV_SMTP_FROM = "SMTP_FROM"
ENV_SMTP_TO = "SMTP_TO"
ENV_FAILSAFE_DIR = "EDGE_EQUATION_FAILSAFE_DIR"


class FileFailsafe:
    """
    Filesystem failsafe:
    - deliver(subject, body, target='x', now=None) -> str
    - writes data/failsafes/{target}-{timestamp}.txt
    - returns the absolute file path on success
    """

    def __init__(self, directory: Optional[str] = None):
        self.directory = directory or os.environ.get(ENV_FAILSAFE_DIR) or "data/failsafes"

    def deliver(
        self,
        subject: str,
        body: str,
        target: str = "x",
        now: Optional[datetime] = None,
    ) -> str:
        ts = (now or datetime.utcnow()).strftime("%Y%m%d-%H%M%S")
        path = Path(self.directory)
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / f"{target}-{ts}.txt"
        contents = f"SUBJECT: {subject}\n\n{body}\n"
        file_path.write_text(contents, encoding="utf-8")
        return f"file={file_path}"


class SmtpFailsafe:
    """
    SMTP failsafe via stdlib smtplib:
    - deliver(subject, body, target='x', now=None) -> str
    - requires SMTP_HOST at construction (kwarg or env var); missing -> raises
    - STARTTLS + login when user/password are provided
    - returns "smtp={to_addr}" on success
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        from_addr: Optional[str] = None,
        to_addr: Optional[str] = None,
        smtp_factory=None,
    ):
        self.host = host if host is not None else os.environ.get(ENV_SMTP_HOST)
        if not self.host:
            raise ValueError(f"SmtpFailsafe requires host or {ENV_SMTP_HOST} env var")
        port_raw = port if port is not None else os.environ.get(ENV_SMTP_PORT)
        self.port = int(port_raw) if port_raw else 587
        self.user = user if user is not None else os.environ.get(ENV_SMTP_USER)
        self.password = password if password is not None else os.environ.get(ENV_SMTP_PASSWORD)
        self.from_addr = from_addr if from_addr is not None else os.environ.get(ENV_SMTP_FROM)
        self.to_addr = to_addr if to_addr is not None else os.environ.get(ENV_SMTP_TO)
        if not self.from_addr or not self.to_addr:
            raise ValueError(
                f"SmtpFailsafe requires from_addr + to_addr (or {ENV_SMTP_FROM} / {ENV_SMTP_TO})"
            )
        # smtp_factory is injectable for tests; defaults to smtplib.SMTP.
        self._factory = smtp_factory or smtplib.SMTP

    def deliver(
        self,
        subject: str,
        body: str,
        target: str = "x",
        now: Optional[datetime] = None,
    ) -> str:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        msg.set_content(body)

        with self._factory(self.host, self.port) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls()
                smtp.ehlo()
            except smtplib.SMTPException:
                # Server does not support STARTTLS; continue unencrypted.
                pass
            if self.user and self.password:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)
        return f"smtp={self.to_addr}"


class CompositeFailsafe:
    """
    Try every configured failsafe in order; return a semicolon-joined detail
    string with the outcome of each. One failure does not abort the others --
    the operator benefits from redundancy.
    """

    def __init__(self, handlers: List[object]):
        self.handlers = list(handlers)

    def deliver(
        self,
        subject: str,
        body: str,
        target: str = "x",
        now: Optional[datetime] = None,
    ) -> str:
        if not self.handlers:
            return "none"
        parts: List[str] = []
        for h in self.handlers:
            try:
                detail = h.deliver(subject=subject, body=body, target=target, now=now)
                parts.append(f"{type(h).__name__}:{detail}")
            except Exception as e:
                parts.append(f"{type(h).__name__}:FAILED({type(e).__name__}: {e})")
        return " ; ".join(parts)


def default_failsafe(failsafe_dir: Optional[str] = None) -> Optional[CompositeFailsafe]:
    """
    Auto-configured composite failsafe:
    - Always includes a FileFailsafe (zero-config).
    - Adds SmtpFailsafe iff SMTP_HOST, SMTP_FROM, SMTP_TO env vars are all set.
    Returns None only if FileFailsafe cannot be constructed (practically
    impossible in a writable filesystem).
    """
    handlers: List[object] = []
    try:
        handlers.append(FileFailsafe(directory=failsafe_dir))
    except Exception:
        pass
    if os.environ.get(ENV_SMTP_HOST) and os.environ.get(ENV_SMTP_FROM) and os.environ.get(ENV_SMTP_TO):
        try:
            handlers.append(SmtpFailsafe())
        except Exception:
            pass
    if not handlers:
        return None
    return CompositeFailsafe(handlers)
