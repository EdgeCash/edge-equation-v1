"""
Email publisher.

Sends a card as a plain-text email via stdlib smtplib (STARTTLS + optional
login). Reuses the same SMTP config as failsafe.SmtpFailsafe; the publisher
picks its own recipient (EMAIL_TO) but otherwise shares host / port /
credentials / from-address with the failsafe path.

Config:
- SMTP_HOST                required (primary mail server)
- SMTP_PORT                default 587
- SMTP_USER                optional; when set, login is attempted
- SMTP_PASSWORD            optional
- SMTP_FROM                required (From: address)
- EMAIL_TO                 where daily publications go; falls back to SMTP_TO
- SMTP_TO                  fallback recipient (same as failsafe)

Failsafe: same contract as XPublisher / DiscordPublisher. If SMTP fails, the
file failsafe still captures the intended message so nothing is lost.
"""
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from edge_equation.publishing.base_publisher import PublishResult
from edge_equation.publishing.failsafe import default_failsafe


ENV_SMTP_HOST = "SMTP_HOST"
ENV_SMTP_PORT = "SMTP_PORT"
ENV_SMTP_USER = "SMTP_USER"
ENV_SMTP_PASSWORD = "SMTP_PASSWORD"
ENV_SMTP_FROM = "SMTP_FROM"
ENV_EMAIL_TO = "EMAIL_TO"
ENV_SMTP_TO = "SMTP_TO"


class EmailPublisher:
    """
    Real SMTP email publisher:
    - publish_card(card, dry_run=False) -> PublishResult
    - build_subject(card)               -> str
    - build_body(card)                  -> str
    Credentials from kwargs > env vars. smtp_factory is injectable for tests.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        from_address: Optional[str] = None,
        to_address: Optional[str] = None,
        smtp_factory=None,
        failsafe: Optional[object] = None,
        body_formatter=None,
        subject_prefix: Optional[str] = None,
    ):
        self.host = host if host is not None else os.environ.get(ENV_SMTP_HOST)
        port_raw = port if port is not None else os.environ.get(ENV_SMTP_PORT)
        self.port = int(port_raw) if port_raw else 587
        self.user = user if user is not None else os.environ.get(ENV_SMTP_USER)
        self.password = password if password is not None else os.environ.get(ENV_SMTP_PASSWORD)
        self.from_address = (
            from_address if from_address is not None
            else os.environ.get(ENV_SMTP_FROM) or "edge@edgeequation.com"
        )
        self.to_address = (
            to_address if to_address is not None
            else os.environ.get(ENV_EMAIL_TO) or os.environ.get(ENV_SMTP_TO)
        )
        self._factory = smtp_factory or smtplib.SMTP
        if failsafe is None:
            self._failsafe = default_failsafe()
        elif failsafe is False:
            self._failsafe = None
        else:
            self._failsafe = failsafe
        # Injectable body formatter. Useful for email-preview mode where
        # we want the email body to be byte-identical to what XPublisher
        # would post. Default: the legacy structured email layout below.
        self._body_formatter = body_formatter
        self._subject_prefix = subject_prefix

    @staticmethod
    def build_subject(card: dict) -> str:
        card_type = card.get("card_type") or "card"
        date = (card.get("generated_at") or "").split("T")[0] or "today"
        return f"Edge Equation – {card_type} – {date}"

    @staticmethod
    def build_body(card: dict) -> str:
        headline = card.get("headline") or ""
        subhead = card.get("subhead") or ""
        tagline = card.get("tagline") or ""
        picks = card.get("picks") or []

        lines = []
        if headline:
            lines.append(headline)
        if subhead:
            lines.append(subhead)
        lines.append("")

        for p in picks:
            parts = [f"- {p.get('market_type', '?')}: {p.get('selection', '?')}"]
            grade = p.get("grade")
            if grade:
                parts.append(f"Grade: {grade}")
            if p.get("edge") is not None:
                parts.append(f"Edge: {p['edge']}")
            if p.get("kelly") is not None:
                parts.append(f"½ Kelly: {p['kelly']}")
            if p.get("fair_prob") is not None:
                parts.append(f"Fair Prob: {p['fair_prob']}")
            if p.get("expected_value") is not None:
                parts.append(f"Expected: {p['expected_value']}")
            lines.append(" | ".join(parts))

        if tagline:
            lines.append("")
            lines.append(tagline)
        return "\n".join(lines)

    def _missing_config(self) -> Optional[str]:
        missing = []
        if not self.host: missing.append(ENV_SMTP_HOST)
        if not self.from_address: missing.append(ENV_SMTP_FROM)
        if not self.to_address: missing.append(f"{ENV_EMAIL_TO} or {ENV_SMTP_TO}")
        if missing:
            return f"missing credentials: {', '.join(missing)}"
        return None

    def _fire_failsafe(self, subject: str, body: str, error: str) -> tuple:
        if self._failsafe is None:
            return False, None
        try:
            safe_subject = f"[Edge Equation] Email publisher failsafe ({error[:80]})"
            safe_body = (
                f"The primary email send failed with: {error}\n\n"
                f"Intended subject: {subject}\n\n{body}\n"
            )
            detail = self._failsafe.deliver(
                subject=safe_subject, body=safe_body, target="email",
            )
            return True, detail
        except Exception as e:
            return False, f"failsafe itself failed: {e}"

    def publish_card(self, card_payload: dict, dry_run: bool = False) -> PublishResult:
        try:
            subject = self.build_subject(card_payload)
            if self._subject_prefix:
                subject = f"{self._subject_prefix} {subject}"
            if self._body_formatter is not None:
                body = self._body_formatter(card_payload)
            else:
                body = self.build_body(card_payload)
        except Exception as e:
            return PublishResult(success=False, target="email", error=f"build error: {e}")

        if dry_run:
            return PublishResult(success=True, target="email", message_id="dry-run")

        missing = self._missing_config()
        if missing:
            fired, detail = self._fire_failsafe(subject, body, missing)
            return PublishResult(
                success=False, target="email", error=missing,
                failsafe_triggered=fired, failsafe_detail=detail,
            )

        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.from_address
            msg["To"] = self.to_address
            msg.set_content(body)

            with self._factory(self.host, self.port) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls()
                    smtp.ehlo()
                except smtplib.SMTPException:
                    pass
                if self.user and self.password:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)

            return PublishResult(
                success=True, target="email",
                message_id=f"email-to-{self.to_address}",
            )
        except Exception as e:
            err = str(e)
            fired, detail = self._fire_failsafe(subject, body, err)
            return PublishResult(
                success=False, target="email", error=err,
                failsafe_triggered=fired, failsafe_detail=detail,
            )
