"""
Magic-link email sender.

Uses the same stdlib smtplib + env-var config as the email publisher and
SMTP failsafe. Zero new dependencies.

The raw token is rendered into a URL like:
    https://edgeequation.com/auth/verify?token=<raw>

Configure the public URL via WEBSITE_BASE_URL; defaults to
http://localhost:3000 so local dev works out of the box.
"""
import os
import smtplib
from email.message import EmailMessage
from typing import Optional


ENV_SMTP_HOST = "SMTP_HOST"
ENV_SMTP_PORT = "SMTP_PORT"
ENV_SMTP_USER = "SMTP_USER"
ENV_SMTP_PASSWORD = "SMTP_PASSWORD"
ENV_SMTP_FROM = "SMTP_FROM"
ENV_WEBSITE_BASE_URL = "WEBSITE_BASE_URL"


def _website_base() -> str:
    return os.environ.get(ENV_WEBSITE_BASE_URL, "http://localhost:3000").rstrip("/")


def build_magic_link_url(raw_token: str, base_url: Optional[str] = None) -> str:
    base = (base_url or _website_base()).rstrip("/")
    return f"{base}/auth/verify?token={raw_token}"


def build_subject() -> str:
    return "Your Edge Equation sign-in link"


def build_body(raw_token: str, base_url: Optional[str] = None) -> str:
    link = build_magic_link_url(raw_token, base_url=base_url)
    return (
        "Click the link below to sign in to Edge Equation. "
        "The link expires in 15 minutes and can only be used once.\n\n"
        f"{link}\n\n"
        "If you didn't request this email, ignore it -- nothing happens until "
        "the link is clicked.\n\n"
        "-- Edge Equation (Facts. Not Feelings.)\n"
    )


class MagicLinkSender:
    """
    SMTP magic-link sender:
    - send(to_email, raw_token, base_url=None, smtp_factory=None) -> bool
    Raises RuntimeError if required SMTP env vars are not set. Returns True
    on a successful send; the caller treats exceptions as soft failures and
    retries / notifies the operator via the existing failsafe chain.
    """

    @staticmethod
    def send(
        to_email: str,
        raw_token: str,
        base_url: Optional[str] = None,
        smtp_factory=None,
    ) -> bool:
        host = os.environ.get(ENV_SMTP_HOST)
        if not host:
            raise RuntimeError(f"{ENV_SMTP_HOST} not set; cannot send magic link")
        port = int(os.environ.get(ENV_SMTP_PORT) or 587)
        user = os.environ.get(ENV_SMTP_USER)
        password = os.environ.get(ENV_SMTP_PASSWORD)
        from_addr = os.environ.get(ENV_SMTP_FROM)
        if not from_addr:
            raise RuntimeError(f"{ENV_SMTP_FROM} not set; cannot send magic link")

        msg = EmailMessage()
        msg["Subject"] = build_subject()
        msg["From"] = from_addr
        msg["To"] = to_email
        msg.set_content(build_body(raw_token, base_url=base_url))

        factory = smtp_factory or smtplib.SMTP
        with factory(host, port) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls()
                smtp.ehlo()
            except smtplib.SMTPException:
                pass
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True
