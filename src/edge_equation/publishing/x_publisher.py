"""
X (Twitter) publisher.

Posts a formatted card as a tweet via the v2 POST /2/tweets endpoint using
OAuth 1.0a user-context signing. Credentials come from env vars unless passed
explicitly to the constructor:

    X_API_KEY              consumer key
    X_API_SECRET           consumer secret
    X_ACCESS_TOKEN         user access token
    X_ACCESS_TOKEN_SECRET  user access token secret

Format is rich by default (PremiumFormatter, 25K char ceiling). Pass
style='standard' for the legacy 280-char behavior -- useful when cross-posting
to platforms with the same limit.

Failsafe: on ANY failure of the primary post path (missing credentials, HTTP
error, transport error), XPublisher hands the rendered post text to a
failsafe (FileFailsafe by default, optional SmtpFailsafe via env vars) so the
operator can manually repost. After the failsafe fires the publisher does NOT
retry -- a retry-after-failsafe risks a double post.

dry_run=True short-circuits before any network call or failsafe and returns a
deterministic PublishResult with message_id='dry-run'.

No exceptions escape publish_card; every failure surfaces via PublishResult.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional
from urllib.parse import quote

import httpx

from edge_equation.publishing.base_publisher import PublishResult
from edge_equation.publishing.x_formatter import (
    PREMIUM_MAX_LEN,
    STANDARD_MAX_LEN,
    format_card,
)
from edge_equation.publishing.failsafe import default_failsafe


TWEETS_ENDPOINT = "https://api.twitter.com/2/tweets"

ENV_API_KEY = "X_API_KEY"
ENV_API_SECRET = "X_API_SECRET"
ENV_ACCESS_TOKEN = "X_ACCESS_TOKEN"
ENV_ACCESS_TOKEN_SECRET = "X_ACCESS_TOKEN_SECRET"


# Exposed for legacy test compatibility; the publisher itself routes through
# the formatter's PREMIUM_MAX_LEN by default.
MAX_LEN = STANDARD_MAX_LEN


class XPublisher:
    """
    Real X publisher:
    - publish_card(card, dry_run=False)      -> PublishResult
    - format_card(card)                      -> str (static helper on formatter)
    Constructor reads credentials from kwargs > env vars. Injectable http_client
    keeps tests deterministic (see tests/test_publishing_x.py).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        access_token_secret: Optional[str] = None,
        style: str = "premium",
        max_len: Optional[int] = None,
        http_client: Optional[httpx.Client] = None,
        failsafe: Optional[object] = None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get(ENV_API_KEY)
        self.api_secret = api_secret if api_secret is not None else os.environ.get(ENV_API_SECRET)
        self.access_token = access_token if access_token is not None else os.environ.get(ENV_ACCESS_TOKEN)
        self.access_token_secret = (
            access_token_secret
            if access_token_secret is not None
            else os.environ.get(ENV_ACCESS_TOKEN_SECRET)
        )
        if style not in ("premium", "standard"):
            raise ValueError(f"style must be 'premium' or 'standard', got {style!r}")
        self.style = style
        self.max_len = max_len if max_len is not None else (
            PREMIUM_MAX_LEN if style == "premium" else STANDARD_MAX_LEN
        )
        self._http_client = http_client
        # Sentinel object() opts out of the auto-configured failsafe; None =
        # auto-configure via default_failsafe(); anything else = caller's
        # pre-built failsafe handler.
        if failsafe is None:
            self._failsafe = default_failsafe()
        elif failsafe is False:
            self._failsafe = None
        else:
            self._failsafe = failsafe

    # ------------------------------------------------------------------ OAuth

    @staticmethod
    def _pct_encode(value: str) -> str:
        # RFC 3986 unreserved set used by OAuth 1.0a percent-encoding
        return quote(str(value), safe="-._~")

    @staticmethod
    def _build_oauth_header(
        method: str,
        url: str,
        consumer_key: str,
        consumer_secret: str,
        access_token: str,
        access_token_secret: str,
        nonce: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> str:
        """
        Build an OAuth 1.0a Authorization header for a POST /2/tweets request.

        The v2 tweets endpoint takes a JSON body; per the OAuth 1.0a spec, the
        signature base string does NOT include the JSON body (only
        application/x-www-form-urlencoded bodies contribute params). So the
        base string is built from the oauth_* params alone, plus the method
        and the request URL.

        nonce and timestamp are injectable for deterministic testing.
        """
        oauth_params = {
            "oauth_consumer_key": consumer_key,
            "oauth_nonce": nonce if nonce is not None else secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": timestamp if timestamp is not None else str(int(time.time())),
            "oauth_token": access_token,
            "oauth_version": "1.0",
        }
        # Collect all signing params (no query-string or form body here);
        # sort by key, then concatenate with OAuth percent-encoding.
        sorted_pairs = sorted(oauth_params.items())
        param_string = "&".join(
            f"{XPublisher._pct_encode(k)}={XPublisher._pct_encode(v)}"
            for k, v in sorted_pairs
        )
        base_string = "&".join([
            method.upper(),
            XPublisher._pct_encode(url),
            XPublisher._pct_encode(param_string),
        ])
        signing_key = f"{XPublisher._pct_encode(consumer_secret)}&{XPublisher._pct_encode(access_token_secret)}"
        signature = base64.b64encode(
            hmac.new(signing_key.encode("ascii"), base_string.encode("ascii"), hashlib.sha1).digest()
        ).decode("ascii")
        oauth_params["oauth_signature"] = signature

        header_pairs = sorted(oauth_params.items())
        header = "OAuth " + ", ".join(
            f'{XPublisher._pct_encode(k)}="{XPublisher._pct_encode(v)}"'
            for k, v in header_pairs
        )
        return header

    def _missing_credentials(self) -> Optional[str]:
        missing = []
        if not self.api_key: missing.append(ENV_API_KEY)
        if not self.api_secret: missing.append(ENV_API_SECRET)
        if not self.access_token: missing.append(ENV_ACCESS_TOKEN)
        if not self.access_token_secret: missing.append(ENV_ACCESS_TOKEN_SECRET)
        if missing:
            return f"missing credentials: {', '.join(missing)}"
        return None

    # --------------------------------------------------------- Public surface

    def format_text(self, card: dict) -> str:
        return format_card(card, style=self.style, max_len=self.max_len)

    def _fire_failsafe(self, text: str, error: str) -> tuple:
        """Run the configured failsafe. Returns (triggered_bool, detail_str)."""
        if self._failsafe is None:
            return False, None
        try:
            subject = f"[Edge Equation] X post failsafe triggered ({error[:80]})"
            body = (
                f"The primary X post failed with: {error}\n\n"
                f"The intended post text is below. Post it manually.\n\n"
                f"{'-' * 40}\n{text}\n{'-' * 40}\n"
            )
            detail = self._failsafe.deliver(subject=subject, body=body, target="x")
            return True, detail
        except Exception as e:
            return False, f"failsafe itself failed: {e}"

    def publish_card(
        self,
        card_payload: dict,
        dry_run: bool = False,
        nonce: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> PublishResult:
        # Render text up front so any failsafe has something to deliver.
        try:
            text = self.format_text(card_payload)
        except Exception as e:
            return PublishResult(success=False, target="x", error=f"format error: {e}")

        if len(text) > self.max_len:
            return PublishResult(
                success=False, target="x",
                error=f"text too long after truncation ({len(text)} chars)",
            )

        # Phase 20 brand gate: every public-mode post (disclaimer already
        # injected into the tagline by PostingFormatter.build_card) has to
        # pass compliance_test with the Season Ledger footer required.
        # Premium / internal posts (no public_mode -> no disclaimer) are
        # not subject to this gate.
        from edge_equation.compliance import compliance_test
        from edge_equation.compliance.disclaimer import DISCLAIMER_TEXT
        is_public = DISCLAIMER_TEXT in (card_payload.get("tagline") or "")
        if is_public:
            report = compliance_test(text, require_ledger_footer=True)
            if not report.ok:
                fired, detail = self._fire_failsafe(
                    text, f"compliance violations: {report.violations}"
                )
                return PublishResult(
                    success=False, target="x",
                    error=f"compliance blocked: {report.violations}",
                    failsafe_triggered=fired, failsafe_detail=detail,
                )

        if dry_run:
            return PublishResult(success=True, target="x", message_id="dry-run")

        missing = self._missing_credentials()
        if missing:
            fired, detail = self._fire_failsafe(text, missing)
            return PublishResult(
                success=False, target="x", error=missing,
                failsafe_triggered=fired, failsafe_detail=detail,
            )

        try:
            header = XPublisher._build_oauth_header(
                method="POST",
                url=TWEETS_ENDPOINT,
                consumer_key=self.api_key,
                consumer_secret=self.api_secret,
                access_token=self.access_token,
                access_token_secret=self.access_token_secret,
                nonce=nonce,
                timestamp=timestamp,
            )
            headers = {
                "Authorization": header,
                "Content-Type": "application/json",
            }
            body = {"text": text}

            owns_client = self._http_client is None
            client = self._http_client if not owns_client else httpx.Client(timeout=30.0)
            try:
                resp = client.post(TWEETS_ENDPOINT, json=body, headers=headers)
            finally:
                if owns_client:
                    client.close()

            if resp.status_code >= 400:
                err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                fired, detail = self._fire_failsafe(text, err)
                return PublishResult(
                    success=False, target="x", error=err,
                    failsafe_triggered=fired, failsafe_detail=detail,
                )
            payload = resp.json()
            data = payload.get("data") or {}
            tweet_id = data.get("id")
            if not tweet_id:
                err = f"no tweet id in response: {str(payload)[:200]}"
                fired, detail = self._fire_failsafe(text, err)
                return PublishResult(
                    success=False, target="x", error=err,
                    failsafe_triggered=fired, failsafe_detail=detail,
                )
            return PublishResult(success=True, target="x", message_id=f"x-{tweet_id}")

        except Exception as e:
            err = str(e)
            fired, detail = self._fire_failsafe(text, err)
            return PublishResult(
                success=False, target="x", error=err,
                failsafe_triggered=fired, failsafe_detail=detail,
            )
