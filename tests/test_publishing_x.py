import httpx
import pytest

from edge_equation.publishing.x_publisher import (
    XPublisher,
    ENV_API_KEY,
    ENV_API_SECRET,
    ENV_ACCESS_TOKEN,
    ENV_ACCESS_TOKEN_SECRET,
    MAX_LEN,
    TWEETS_ENDPOINT,
)
from edge_equation.publishing.x_formatter import PREMIUM_MAX_LEN, STANDARD_MAX_LEN


def _card(headline="Daily Edge", n_picks=2):
    return {
        "card_type": "daily_edge",
        "headline": headline,
        "subhead": "Today's model-graded plays.",
        "picks": [
            {"sport": "MLB", "market_type": "ML", "selection": "BOS",
             "grade": "A", "edge": "0.049167", "fair_prob": "0.553412",
             "kelly": "0.0085", "line": {"odds": -132, "number": None},
             "game_id": "MLB-2026-04-20-DET-BOS"},
            {"sport": "MLB", "market_type": "Total", "selection": "Over 9.5",
             "grade": "C", "edge": None, "expected_value": "9.78",
             "line": {"odds": -110, "number": "9.5"},
             "game_id": "MLB-2026-04-20-DET-BOS"},
        ][:n_picks],
        "tagline": "Facts. Not Feelings.",
        "summary": {"grade": "A", "edge": "0.049167", "kelly": "0.0085"},
        "generated_at": "2026-04-20T09:00:00",
    }


def _creds():
    return dict(
        api_key="CKEY", api_secret="CSEC",
        access_token="TOK",  access_token_secret="TSEC",
    )


def _pub(**overrides):
    """Build a publisher with auto-failsafe disabled; tests opt-in per-case."""
    kwargs = {**_creds(), "failsafe": False}
    kwargs.update(overrides)
    return XPublisher(**kwargs)


def _mock_success_client(capture):
    def handler(request):
        capture["url"] = str(request.url)
        capture["headers"] = dict(request.headers)
        capture["body"] = request.content.decode()
        capture["method"] = request.method
        return httpx.Response(200, json={"data": {"id": "1831234567890", "text": "ok"}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _mock_error_client(status: int, body: dict):
    def handler(request):
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


# -------------------------------------------------------------------- dry-run


def test_dry_run_returns_success():
    pub = _pub()
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "x"
    assert result.message_id == "dry-run"
    assert result.error is None
    assert result.failsafe_triggered is False


def test_dry_run_without_credentials_still_works():
    # dry_run short-circuits BEFORE credential check
    pub = XPublisher(failsafe=False)
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.message_id == "dry-run"


# ----------------------------------------------------------------- formatting


def test_premium_formatter_used_by_default():
    pub = _pub()
    text = pub.format_text(_card())
    assert "━━━" in text
    assert "DAILY EDGE" in text


def test_standard_style_respects_280_cap():
    pub = _pub(style="standard")
    assert pub.max_len == STANDARD_MAX_LEN
    text = pub.format_text(_card())
    assert len(text) <= STANDARD_MAX_LEN


def test_premium_style_uses_25k_cap_by_default():
    pub = _pub()
    assert pub.max_len == PREMIUM_MAX_LEN


def test_invalid_style_rejected():
    with pytest.raises(ValueError, match="style must be"):
        XPublisher(**_creds(), style="weird", failsafe=False)


# ------------------------------------------------------------- credentials


def test_missing_credentials_returns_failure_non_dry_run():
    pub = XPublisher(failsafe=False)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert result.target == "x"
    assert result.error is not None
    assert "missing credentials" in result.error


def test_credentials_loaded_from_env_vars(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "ENV_CK")
    monkeypatch.setenv(ENV_API_SECRET, "ENV_CS")
    monkeypatch.setenv(ENV_ACCESS_TOKEN, "ENV_AT")
    monkeypatch.setenv(ENV_ACCESS_TOKEN_SECRET, "ENV_ATS")
    pub = XPublisher(failsafe=False)
    assert pub.api_key == "ENV_CK"
    assert pub.api_secret == "ENV_CS"
    assert pub.access_token == "ENV_AT"
    assert pub.access_token_secret == "ENV_ATS"


def test_explicit_kwargs_override_env(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "ENV_CK")
    pub = XPublisher(
        api_key="KWARG_CK", api_secret="x", access_token="y",
        access_token_secret="z", failsafe=False,
    )
    assert pub.api_key == "KWARG_CK"


# ------------------------------------------------------- OAuth 1.0a signing


def test_oauth_header_deterministic_with_fixed_nonce_and_timestamp():
    h1 = XPublisher._build_oauth_header(
        method="POST", url=TWEETS_ENDPOINT,
        consumer_key="CK", consumer_secret="CS",
        access_token="AT", access_token_secret="ATS",
        nonce="fixed_nonce_12345", timestamp="1700000000",
    )
    h2 = XPublisher._build_oauth_header(
        method="POST", url=TWEETS_ENDPOINT,
        consumer_key="CK", consumer_secret="CS",
        access_token="AT", access_token_secret="ATS",
        nonce="fixed_nonce_12345", timestamp="1700000000",
    )
    assert h1 == h2


def test_oauth_header_contains_required_fields():
    h = XPublisher._build_oauth_header(
        method="POST", url=TWEETS_ENDPOINT,
        consumer_key="CK", consumer_secret="CS",
        access_token="AT", access_token_secret="ATS",
        nonce="nonce", timestamp="1700000000",
    )
    assert h.startswith("OAuth ")
    for field in (
        "oauth_consumer_key=",
        "oauth_nonce=",
        "oauth_signature=",
        "oauth_signature_method=\"HMAC-SHA1\"",
        "oauth_timestamp=",
        "oauth_token=",
        "oauth_version=\"1.0\"",
    ):
        assert field in h, f"missing {field} in header"


def test_oauth_signature_changes_with_different_secret():
    h1 = XPublisher._build_oauth_header(
        method="POST", url=TWEETS_ENDPOINT,
        consumer_key="CK", consumer_secret="CS1",
        access_token="AT", access_token_secret="ATS",
        nonce="N", timestamp="T",
    )
    h2 = XPublisher._build_oauth_header(
        method="POST", url=TWEETS_ENDPOINT,
        consumer_key="CK", consumer_secret="CS2",
        access_token="AT", access_token_secret="ATS",
        nonce="N", timestamp="T",
    )
    assert h1 != h2


def test_oauth_signature_handles_special_chars_in_tokens():
    h = XPublisher._build_oauth_header(
        method="POST", url=TWEETS_ENDPOINT,
        consumer_key="CK/slashes",
        consumer_secret="CS spaces",
        access_token="AT",
        access_token_secret="AT+S=1",
        nonce="N", timestamp="T",
    )
    assert "OAuth " in h


# ------------------------------------------------------ real POST (mocked)


def test_non_dry_run_posts_to_correct_endpoint():
    capture = {}
    pub = _pub(http_client=_mock_success_client(capture))
    result = pub.publish_card(_card(), dry_run=False, nonce="N", timestamp="T")
    assert result.success is True
    assert result.message_id == "x-1831234567890"
    assert capture["url"] == TWEETS_ENDPOINT
    assert capture["method"] == "POST"


def test_non_dry_run_sends_authorization_header():
    capture = {}
    pub = _pub(http_client=_mock_success_client(capture))
    pub.publish_card(_card(), dry_run=False, nonce="N", timestamp="T")
    auth = capture["headers"].get("authorization") or capture["headers"].get("Authorization")
    assert auth is not None
    assert auth.startswith("OAuth ")
    assert 'oauth_consumer_key="CKEY"' in auth


def test_non_dry_run_sends_json_body_with_text():
    import json as _json
    capture = {}
    pub = _pub(http_client=_mock_success_client(capture))
    pub.publish_card(_card(), dry_run=False, nonce="N", timestamp="T")
    body = _json.loads(capture["body"])
    assert "text" in body
    assert "DAILY EDGE" in body["text"]


def test_http_401_is_surfaced_as_failure():
    pub = _pub(http_client=_mock_error_client(401, {"title": "Unauthorized"}))
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert "401" in (result.error or "")


def test_http_429_rate_limit_surfaced():
    pub = _pub(http_client=_mock_error_client(429, {"title": "Too Many Requests"}))
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert "429" in (result.error or "")


def test_response_missing_tweet_id_surfaces_error():
    def handler(request):
        return httpx.Response(200, json={"data": {}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = _pub(http_client=client)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert "tweet id" in (result.error or "")


def test_publish_card_never_raises_on_transport_error():
    def handler(request):
        raise httpx.ConnectError("network down")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = _pub(http_client=client)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert result.error  # descriptive message, not an exception


# ----------------------------------------------------- failsafe integration


class _StubFailsafe:
    def __init__(self):
        self.calls = []

    def deliver(self, subject, body, target="x", now=None):
        self.calls.append({"subject": subject, "body": body, "target": target})
        return f"stub:{len(self.calls)}"


def test_failsafe_fires_on_missing_credentials():
    fs = _StubFailsafe()
    pub = XPublisher(failsafe=fs)  # no credentials
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert result.failsafe_triggered is True
    assert result.failsafe_detail == "stub:1"
    assert len(fs.calls) == 1
    assert "DAILY EDGE" in fs.calls[0]["body"]


def test_failsafe_fires_on_http_error():
    fs = _StubFailsafe()
    pub = XPublisher(**_creds(), failsafe=fs,
                     http_client=_mock_error_client(401, {"title": "Unauthorized"}))
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert result.failsafe_triggered is True
    assert "401" in (result.error or "")
    assert len(fs.calls) == 1


def test_failsafe_fires_on_transport_error():
    fs = _StubFailsafe()
    def handler(request):
        raise httpx.ConnectError("network down")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = XPublisher(**_creds(), failsafe=fs, http_client=client)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.failsafe_triggered is True
    assert len(fs.calls) == 1


def test_failsafe_not_fired_on_success():
    fs = _StubFailsafe()
    capture = {}
    pub = XPublisher(**_creds(), failsafe=fs, http_client=_mock_success_client(capture))
    result = pub.publish_card(_card(), dry_run=False, nonce="N", timestamp="T")
    assert result.success is True
    assert result.failsafe_triggered is False
    assert fs.calls == []


def test_failsafe_not_fired_on_dry_run():
    fs = _StubFailsafe()
    pub = XPublisher(**_creds(), failsafe=fs)
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.failsafe_triggered is False
    assert fs.calls == []


def test_failsafe_disabled_with_false():
    pub = XPublisher(failsafe=False)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert result.failsafe_triggered is False
    assert result.failsafe_detail is None


def test_failsafe_after_failure_no_retry():
    """
    After the failsafe fires, the publisher must NOT retry the post. We
    verify this by counting HTTP hits: a failed post should produce exactly
    one call to the mocked transport.
    """
    fs = _StubFailsafe()
    hits = {"count": 0}
    def handler(request):
        hits["count"] += 1
        return httpx.Response(500, json={"title": "Server error"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = XPublisher(**_creds(), failsafe=fs, http_client=client)
    pub.publish_card(_card(), dry_run=False)
    assert hits["count"] == 1  # no retry after failsafe


def test_failsafe_body_contains_would_be_post_text():
    fs = _StubFailsafe()
    pub = XPublisher(failsafe=fs)
    pub.publish_card(_card(), dry_run=False)
    body = fs.calls[0]["body"]
    assert "The primary X post failed" in body
    assert "Post it manually" in body
    assert "━━━" in body  # the premium-formatted text is included


def test_failsafe_internal_error_surfaces_without_raising():
    class BoomFailsafe:
        def deliver(self, subject, body, target="x", now=None):
            raise RuntimeError("file write failed")
    pub = XPublisher(failsafe=BoomFailsafe())
    result = pub.publish_card(_card(), dry_run=False)
    # Missing creds -> failsafe called -> failsafe itself errors -> still captured
    assert result.success is False
    assert "failsafe itself failed" in (result.failsafe_detail or "")


# -------------------------------------------------------- legacy constant


def test_legacy_max_len_constant_still_exported():
    # Back-compat: external callers may import MAX_LEN from x_publisher.
    assert MAX_LEN == STANDARD_MAX_LEN
