"""
That K Report -- minimal SMTP mailer.

Side-project mailer that reuses the SAME environment variables the
main Edge Equation engine's EmailPublisher consumes -- so no new
GitHub Secrets are needed.  Stdlib smtplib only; injectable
smtp_factory for tests so the suite never reaches the real network.

Why a separate module instead of reusing publishing/email_publisher.py?
    The main engine's EmailPublisher couples to a Card schema +
    failsafe pipeline + body_formatter abstraction.  That's the
    right shape for the multi-section Premium Daily flow.  The
    K-Report side-project just wants "given a subject and a plain
    text body, send it".  Keeping the K-Report mailer narrow keeps
    iteration here from accidentally pulling in main-engine
    dependencies.

Env vars (all reused from the main engine, no new secrets needed):
    SMTP_HOST       required
    SMTP_PORT       default 587 (STARTTLS); 465 -> implicit SSL
    SMTP_USER       optional; when set with SMTP_PASSWORD, login fires
    SMTP_PASSWORD   optional
    SMTP_FROM       required (From: address)
    EMAIL_TO        primary recipient; falls back to SMTP_TO
    SMTP_TO         legacy fallback recipient name
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Dict, Optional


# Env var names -- mirror EmailPublisher constants exactly so
# operators don't have to learn a new set.
ENV_SMTP_HOST = "SMTP_HOST"
ENV_SMTP_PORT = "SMTP_PORT"
ENV_SMTP_USER = "SMTP_USER"
ENV_SMTP_PASSWORD = "SMTP_PASSWORD"
ENV_SMTP_FROM = "SMTP_FROM"
ENV_EMAIL_TO = "EMAIL_TO"
ENV_SMTP_TO = "SMTP_TO"

# Port 465 = implicit SSL (Gmail / most hosted providers).  Anything
# else falls back to plain SMTP + explicit STARTTLS.
_IMPLICIT_SSL_PORTS = frozenset({465})

# Default recipient when EMAIL_TO / SMTP_TO are both unset.  Matches
# the rest of the cadence's default so a misconfigured workflow
# routes to the same inbox the main engine already feeds.
DEFAULT_RECIPIENT = "ProfessorEdgeCash@gmail.com"


class MailError(RuntimeError):
    """Raised when the mailer can't send.  Carries enough context
    that the operator sees what to fix without secrets leaking."""


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    user: Optional[str]
    password: Optional[str]
    sender: str
    recipient: str
    use_ssl: bool

    def to_dict(self) -> dict:
        # Audit trail only -- never expose the password.
        return {
            "host": self.host,
            "port": self.port,
            "user_set": bool(self.user),
            "password_set": bool(self.password),
            "sender": self.sender,
            "recipient": self.recipient,
            "use_ssl": self.use_ssl,
        }


def resolve_mail_config(
    env: Optional[Dict[str, str]] = None,
    *,
    recipient_override: Optional[str] = None,
) -> MailConfig:
    """Read SMTP config from env (or the supplied dict for tests).
    Hard-fails with MailError when required fields are missing.
    """
    env = env if env is not None else os.environ
    host = (env.get(ENV_SMTP_HOST) or "").strip()
    if not host:
        raise MailError(f"missing required env var {ENV_SMTP_HOST}")
    port_raw = env.get(ENV_SMTP_PORT) or "587"
    try:
        port = int(port_raw)
    except (TypeError, ValueError) as e:
        raise MailError(
            f"{ENV_SMTP_PORT}={port_raw!r} is not an integer"
        ) from e
    sender = (env.get(ENV_SMTP_FROM) or "").strip()
    if not sender:
        raise MailError(f"missing required env var {ENV_SMTP_FROM}")
    recipient = (
        (recipient_override or "").strip()
        or (env.get(ENV_EMAIL_TO) or "").strip()
        or (env.get(ENV_SMTP_TO) or "").strip()
        or DEFAULT_RECIPIENT
    )
    user = env.get(ENV_SMTP_USER) or None
    password = env.get(ENV_SMTP_PASSWORD) or None
    return MailConfig(
        host=host, port=port,
        user=user, password=password,
        sender=sender, recipient=recipient,
        use_ssl=(port in _IMPLICIT_SSL_PORTS),
    )


def _default_smtp_factory(config: MailConfig):
    """Pick smtplib.SMTP_SSL or smtplib.SMTP based on the port.
    Auto-STARTTLS happens AFTER connection in the plain branch."""
    if config.use_ssl:
        ctx = ssl.create_default_context()
        return smtplib.SMTP_SSL(config.host, config.port, context=ctx)
    return smtplib.SMTP(config.host, config.port)


def send_email(
    subject: str,
    body: str,
    *,
    config: Optional[MailConfig] = None,
    smtp_factory: Optional[Callable[[MailConfig], object]] = None,
    env: Optional[Dict[str, str]] = None,
    recipient_override: Optional[str] = None,
) -> MailConfig:
    """Send a plain-text email.  Returns the resolved MailConfig
    (audit-friendly, no secrets) on success; raises MailError on
    any failure.

    `smtp_factory` is injectable so unit tests substitute a fake
    SMTP class.  Default is the stdlib smtplib transport with auto
    SSL/STARTTLS based on port.
    """
    if config is None:
        config = resolve_mail_config(env=env, recipient_override=recipient_override)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.sender
    msg["To"] = config.recipient
    msg.set_content(body)

    factory = smtp_factory or _default_smtp_factory
    smtp = factory(config)
    try:
        if not config.use_ssl:
            try:
                smtp.starttls(context=ssl.create_default_context())
            except smtplib.SMTPException as e:
                raise MailError(f"STARTTLS failed: {e}") from e
        if config.user and config.password:
            try:
                smtp.login(config.user, config.password)
            except smtplib.SMTPException as e:
                raise MailError(f"SMTP login failed: {e}") from e
        try:
            smtp.send_message(msg)
        except smtplib.SMTPException as e:
            raise MailError(f"SMTP send failed: {e}") from e
    finally:
        try:
            smtp.quit()
        except Exception:  # noqa: BLE001 -- best-effort close
            pass
    return config
