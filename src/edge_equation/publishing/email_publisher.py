"""
Email publisher.

Sends a card as a plain-text email via stdlib smtplib. Credentials are
read from env by default; tests and library callers may inject via
kwargs. Reuses SMTP_HOST/PORT/USER/PASSWORD/FROM with SmtpFailsafe.

Config (env):
- SMTP_HOST                required (primary mail server)
- SMTP_PORT                default 587; 465 -> implicit SSL; 587 -> STARTTLS
- SMTP_USER                optional; when set with SMTP_PASSWORD, login runs
- SMTP_PASSWORD            optional
- SMTP_FROM                required (From: address)
- EMAIL_TO                 where daily publications go; falls back to SMTP_TO
- SMTP_TO                  fallback recipient (same env var as failsafe)

Logging: every send attempt logs host / port / recipient at INFO. Each
failure mode (missing config, connect error, TLS failure, auth failure,
transport error) logs at WARNING or ERROR with enough detail for a
workflow-log reader to diagnose without re-running locally.

Failsafe: same contract as XPublisher / DiscordPublisher. If SMTP fails,
the composite failsafe captures the intended message so nothing is lost.
Callers that don't want the composite's SMTP-retry leg (which would just
hit the same broken SMTP again) can pass failsafe=FileFailsafe(...)
explicitly.
"""
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from edge_equation.publishing.base_publisher import PublishResult
from edge_equation.publishing.failsafe import default_failsafe
from edge_equation.utils.logging import get_logger


ENV_SMTP_HOST = "SMTP_HOST"
ENV_SMTP_PORT = "SMTP_PORT"
ENV_SMTP_USER = "SMTP_USER"
ENV_SMTP_PASSWORD = "SMTP_PASSWORD"
ENV_SMTP_FROM = "SMTP_FROM"
ENV_EMAIL_TO = "EMAIL_TO"
ENV_SMTP_TO = "SMTP_TO"

# Ports on which smtplib.SMTP_SSL should be used instead of plain SMTP +
# STARTTLS. 465 is the de-facto "implicit SSL" port for Gmail / Yahoo /
# Outlook / most hosted providers.
_IMPLICIT_SSL_PORTS = frozenset({465})

_logger = get_logger("edge-equation.email")


class EmailPublisher:
    """Real SMTP email publisher.

    publish_card(card, dry_run=False) -> PublishResult
    build_subject(card)               -> str
    build_body(card)                  -> str

    Credentials resolution order: kwargs > env vars. smtp_factory is the
    injectable hook tests use to avoid real network -- when set it wins
    over the port-based SSL auto-selection so fake transports still work.
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
        # When a test passes smtp_factory we MUST honor it verbatim --
        # tests inject a context-manager fake that replaces both the
        # SMTP class and SMTP_SSL class. Keep _factory_override as the
        # explicit test hook; _factory stays for backwards-compat shims.
        self._factory_override = smtp_factory
        self._factory = smtp_factory or smtplib.SMTP
        if failsafe is None:
            self._failsafe = default_failsafe()
        elif failsafe is False:
            self._failsafe = None
        else:
            self._failsafe = failsafe
        self._body_formatter = body_formatter
        self._subject_prefix = subject_prefix

    # ------------------------------------------------------------------
    # subject / body

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

        # The card's tagline already carries the Season Ledger footer +
        # disclaimer when public_mode=True; render it verbatim so the
        # compliance footer survives any path that falls through to this
        # default body renderer.
        if tagline:
            lines.append("")
            lines.append(tagline)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # config / SMTP transport

    def _missing_config(self) -> Optional[str]:
        """Return a human-readable list of REQUIRED env vars that are
        not set. SMTP_USER / SMTP_PASSWORD are treated as optional here
        because some relays accept anonymous submission; we log a
        warning in publish_card() if they're missing but the server
        later rejects unauthed sends."""
        missing = []
        if not self.host: missing.append(ENV_SMTP_HOST)
        if not self.from_address: missing.append(ENV_SMTP_FROM)
        if not self.to_address: missing.append(f"{ENV_EMAIL_TO} or {ENV_SMTP_TO}")
        if missing:
            return f"missing credentials: {', '.join(missing)}"
        return None

    def _open_smtp(self):
        """Build an open SMTP / SMTP_SSL connection based on the port.

        Test callers that passed smtp_factory keep deterministic control
        of the returned object -- we don't second-guess their fake.
        Production callers get SSL on 465 and plain SMTP (to be upgraded
        via STARTTLS in publish_card) everywhere else.
        """
        if self._factory_override is not None:
            return self._factory_override(self.host, self.port)
        if self.port in _IMPLICIT_SSL_PORTS:
            # Implicit-SSL port (Gmail / Yahoo / most hosted providers)
            # MUST use SMTP_SSL -- plain SMTP at :465 hangs or is rejected.
            return smtplib.SMTP_SSL(self.host, self.port, timeout=30)
        return smtplib.SMTP(self.host, self.port, timeout=30)

    # ------------------------------------------------------------------
    # failsafe

    def _fire_failsafe(self, subject: str, body: str, error: str) -> tuple:
        if self._failsafe is None:
            _logger.warning("Email publisher: no failsafe configured; intended post discarded")
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
            _logger.info(f"Email publisher: failsafe captured post ({detail})")
            return True, detail
        except Exception as e:
            _logger.error(f"Email publisher: failsafe itself failed: {e}")
            return False, f"failsafe itself failed: {e}"

    # ------------------------------------------------------------------
    # main entry

    def publish_card(self, card_payload: dict, dry_run: bool = False) -> PublishResult:
        # Build subject + body up front so the failsafe always has
        # something to deliver regardless of which leg fails.
        try:
            subject = self.build_subject(card_payload)
            if self._subject_prefix:
                subject = f"{self._subject_prefix} {subject}"
            if self._body_formatter is not None:
                body = self._body_formatter(card_payload)
            else:
                body = self.build_body(card_payload)
        except Exception as e:
            _logger.error(f"Email publisher: body build failed: {e}")
            return PublishResult(success=False, target="email", error=f"build error: {e}")

        if dry_run:
            _logger.info(
                f"Email publisher: dry-run -> subject={subject!r} "
                f"to={self.to_address!r} (no network)"
            )
            return PublishResult(success=True, target="email", message_id="dry-run")

        missing = self._missing_config()
        if missing:
            _logger.warning(f"Email publisher: {missing} -> routing to failsafe")
            fired, detail = self._fire_failsafe(subject, body, missing)
            return PublishResult(
                success=False, target="email", error=missing,
                failsafe_triggered=fired, failsafe_detail=detail,
            )

        # Announce the attempt. One line per send so a workflow log reader
        # can scan runs quickly.
        auth_note = "with auth" if (self.user and self.password) else "anonymous"
        transport = "SMTP_SSL" if self.port in _IMPLICIT_SSL_PORTS else "SMTP+STARTTLS"
        _logger.info(
            f"Email publisher: attempting {transport} to {self.host}:{self.port} "
            f"({auth_note}) -> {self.to_address}"
        )
        if not (self.user and self.password):
            _logger.warning(
                "Email publisher: SMTP_USER / SMTP_PASSWORD not set. Anonymous "
                "SMTP submission is rejected by most hosted providers (Gmail / "
                "Outlook / Yahoo). Set both secrets if sends fail."
            )

        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.from_address
            msg["To"] = self.to_address
            msg.set_content(body)

            with self._open_smtp() as smtp:
                smtp.ehlo()
                # Upgrade to TLS only when we're on a plain-SMTP port.
                # For SMTP_SSL the channel is already encrypted. A failed
                # STARTTLS is NOT silently swallowed anymore -- we log
                # and continue unencrypted so the server can respond
                # with a useful error (better than hanging on send).
                if self.port not in _IMPLICIT_SSL_PORTS:
                    try:
                        smtp.starttls()
                        smtp.ehlo()
                    except smtplib.SMTPException as e:
                        _logger.warning(
                            f"Email publisher: STARTTLS failed ({e}); "
                            f"continuing without TLS (server may reject auth)"
                        )
                if self.user and self.password:
                    try:
                        smtp.login(self.user, self.password)
                    except smtplib.SMTPAuthenticationError as e:
                        _logger.error(
                            f"Email publisher: SMTP auth failed: {e}. "
                            f"For Gmail use an app password, not your account password."
                        )
                        raise
                smtp.send_message(msg)

            _logger.info(
                f"Email publisher: send succeeded -> {self.to_address} (subject={subject!r})"
            )
            return PublishResult(
                success=True, target="email",
                message_id=f"email-to-{self.to_address}",
            )
        except Exception as e:
            err_type = type(e).__name__
            err = f"{err_type}: {e}"
            _logger.error(f"Email publisher: send failed ({err})")
            fired, detail = self._fire_failsafe(subject, body, err)
            return PublishResult(
                success=False, target="email", error=err,
                failsafe_triggered=fired, failsafe_detail=detail,
            )
