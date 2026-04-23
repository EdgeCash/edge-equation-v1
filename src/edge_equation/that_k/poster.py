"""
That K Report -- X poster.

Single-purpose module: OAuth 1.0a signed POST to the X v2 endpoint
`POST https://api.x.com/2/tweets` for publishing one text tweet to
the @ThatK_Guy account.

Stdlib only -- no Tweepy / requests dependency.  HMAC-SHA1 signing
matches the OAuth 1.0a spec X still requires for the v2 tweet-create
endpoint (the bearer-token / OAuth2 user-context paths are gated to
higher tiers).

Test discipline
---------------
The HTTP call goes through an injectable `_opener` so unit tests can
exercise the request shape (URL / method / headers / body) without
ever hitting the real X API.  The CLI `post` subcommand defaults to
DRY-RUN -- nothing leaves the process unless `--live` is explicitly
passed AND the credentials are complete.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets as _secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from edge_equation.that_k.config import XCredentials


# Canonical X v2 tweet-create endpoint.  Post-rebrand the
# api.x.com host is the canonical name; api.twitter.com still
# resolves but we use the new one to match the brand.
X_TWEETS_ENDPOINT = "https://api.x.com/2/tweets"

# X enforces a 280-character limit on the default tweet length.
# The poster doesn't truncate; it warns + lets the operator decide
# whether to retry with shorter text.  Always-on enforcement here
# would silently rewrite the brand's content.
MAX_TWEET_LENGTH = 280


class PostError(RuntimeError):
    """Raised when the X API returns a non-2xx response.  Carries
    the HTTP status code + the X response body so the operator can
    diagnose without re-hitting the API."""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"X API error {status}: {body}")


@dataclass(frozen=True)
class PostResult:
    """Successful tweet-create response."""
    status: int
    tweet_id: Optional[str]
    text: str
    response_body: dict
    request_url: str

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "tweet_id": self.tweet_id,
            "text": self.text,
            "response_body": self.response_body,
            "request_url": self.request_url,
        }


# ---------------------------------------------------- OAuth 1.0a signing

def _percent_encode(s: object) -> str:
    """RFC 3986 percent-encoding.  X accepts `urllib.parse.quote(s,
    safe='')` exactly per the OAuth 1.0a spec -- the default
    `safe='/'` would leak slashes through unencoded which breaks
    the signature base string."""
    return urllib.parse.quote(str(s), safe="")


def _signature_base_string(method: str, url: str, params: Dict[str, str]) -> str:
    """Build the canonical signature base string per OAuth 1.0a
    section 9.1.3.  Params are sorted alphabetically by key, then
    encoded into `key=value` pairs joined by `&`.  The whole param
    string is then percent-encoded once more before being joined
    with method + url."""
    sorted_pairs = sorted(params.items())
    param_str = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted_pairs
    )
    return "&".join((
        method.upper(),
        _percent_encode(url),
        _percent_encode(param_str),
    ))


def _signing_key(consumer_secret: str, token_secret: str) -> str:
    return f"{_percent_encode(consumer_secret)}&{_percent_encode(token_secret)}"


def _hmac_sha1(key: str, message: str) -> str:
    sig = hmac.new(
        key.encode("utf-8"), message.encode("utf-8"), hashlib.sha1,
    ).digest()
    return base64.b64encode(sig).decode("ascii")


def build_oauth_header(
    method: str,
    url: str,
    credentials: XCredentials,
    *,
    nonce: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    """Compose the `Authorization: OAuth ...` header value for a
    request.  `nonce` and `timestamp` are injectable so tests can
    pin them and assert deterministic signatures.

    The v2 `POST /2/tweets` endpoint takes a JSON body, so per the
    OAuth 1.0a spec the body parameters are NOT included in the
    signature base string -- only the OAuth params (and any URL
    query params, of which there are none here).
    """
    nonce = nonce or _secrets.token_hex(16)
    timestamp = timestamp or str(int(time.time()))
    params: Dict[str, str] = {
        "oauth_consumer_key": credentials.api_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp,
        "oauth_token": credentials.access_token,
        "oauth_version": "1.0",
    }
    base = _signature_base_string(method, url, params)
    key = _signing_key(credentials.api_secret, credentials.access_token_secret)
    params["oauth_signature"] = _hmac_sha1(key, base)
    # Header format: comma-separated `key="value"` pairs, all values
    # percent-encoded.  Sort for stable header text (matches Twitter
    # docs example output).
    header_pairs = ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(params.items())
    )
    return "OAuth " + header_pairs


# ---------------------------------------------------- the actual POST

def post_tweet(
    text: str,
    credentials: XCredentials,
    *,
    _opener: Optional[Callable] = None,
) -> PostResult:
    """POST a single text tweet.  Returns PostResult on 2xx, raises
    PostError on 4xx/5xx, propagates URLError on transport failure.

    `_opener` lets tests substitute a fake urlopen-style callable.
    Production code does not pass it -- the default is the stdlib
    urlopen so we don't pull in any HTTP dependency.
    """
    if not credentials.is_complete():
        raise PostError(
            status=0,
            body=(
                f"missing credentials for account "
                f"{credentials.account.value}: {list(credentials.missing)}"
            ),
        )

    body_bytes = json.dumps({"text": text}).encode("utf-8")
    auth = build_oauth_header("POST", X_TWEETS_ENDPOINT, credentials)
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
        "User-Agent": "thatk-pipeline/0.1",
    }
    req = urllib.request.Request(
        X_TWEETS_ENDPOINT, data=body_bytes, headers=headers, method="POST",
    )

    opener = _opener or urllib.request.urlopen
    try:
        with opener(req) as resp:
            raw = resp.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {"raw": raw}
            tweet_id = None
            data_block = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data_block, dict) and data_block.get("id") is not None:
                tweet_id = str(data_block["id"])
            return PostResult(
                status=int(getattr(resp, "status", 200)),
                tweet_id=tweet_id,
                text=text,
                response_body=payload,
                request_url=X_TWEETS_ENDPOINT,
            )
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = str(e)
        raise PostError(status=e.code, body=err_body) from e


# ---------------------------------------------------- canonical test text

def canned_test_text(now: Optional[dt.datetime] = None) -> str:
    """Innocuous, dated, non-tout text the operator can fire to
    verify credentials.  Includes a UTC timestamp so two consecutive
    `--test` runs produce distinct content (X rejects exact-duplicate
    tweets within a short window)."""
    now = now or dt.datetime.utcnow()
    stamp = now.strftime("%Y-%m-%d %H:%M:%SZ")
    return f"Test from That K Report pipeline -- {stamp}"
