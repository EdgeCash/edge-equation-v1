import hashlib
import hmac
import json
import time

import httpx
import pytest

from edge_equation.stripe_client import (
    API_BASE,
    DEFAULT_TOLERANCE_SECONDS,
    ENV_SECRET_KEY,
    ENV_WEBHOOK_SECRET,
    StripeClient,
    StripeError,
    _flatten_form,
)


# --------------------------------------------------- form flattening


def test_flatten_simple_fields():
    out = dict(_flatten_form({"a": "b", "c": 1}))
    assert out == {"a": "b", "c": "1"}


def test_flatten_bool_to_lowercase_literal():
    out = dict(_flatten_form({"x": True, "y": False}))
    assert out == {"x": "true", "y": "false"}


def test_flatten_skips_none_values():
    out = dict(_flatten_form({"a": 1, "b": None}))
    assert out == {"a": "1"}


def test_flatten_nested_dict_uses_bracket_notation():
    out = dict(_flatten_form({"metadata": {"user_id": 42, "tier": "gold"}}))
    assert out == {"metadata[user_id]": "42", "metadata[tier]": "gold"}


def test_flatten_list_of_dicts_uses_indexed_brackets():
    out = dict(_flatten_form({
        "line_items": [{"price": "price_1", "quantity": 1}],
    }))
    assert out == {
        "line_items[0][price]": "price_1",
        "line_items[0][quantity]": "1",
    }


# --------------------------------------------------- HTTP helpers


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_post_requires_api_key():
    client = StripeClient(api_key=None, http_client=_mock_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(StripeError, match="STRIPE_SECRET_KEY"):
        client.create_customer("x@y.com")


def test_post_sends_bearer_and_form():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "cus_TEST"})

    client = StripeClient(api_key="sk_test", http_client=_mock_client(handler))
    result = client.create_customer("x@y.com", metadata={"user_id": 1})
    assert result["id"] == "cus_TEST"
    assert seen["auth"] == "Bearer sk_test"
    assert "application/x-www-form-urlencoded" in seen["content_type"]
    assert "email=x" in seen["body"]
    # Metadata bracket-notation is preserved (x-www-form-urlencoded encodes [ and ])
    body = seen["body"]
    assert "metadata" in body and "user_id" in body


def test_create_checkout_session_shape():
    seen = {}
    def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "cs_TEST", "url": "https://checkout.stripe.com/c/pay/cs_TEST"})
    client = StripeClient(api_key="sk_test", http_client=_mock_client(handler))
    result = client.create_checkout_session(
        customer_id="cus_A",
        price_id="price_B",
        success_url="https://example.com/ok",
        cancel_url="https://example.com/cancel",
        metadata={"user_id": 7},
    )
    assert result["url"].startswith("https://checkout.stripe.com/")
    body = seen["body"]
    assert "mode=subscription" in body
    assert "customer=cus_A" in body
    assert "line_items" in body
    assert "price_B" in body


def test_create_billing_portal_session_endpoint():
    seen = {}
    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": "bps_TEST", "url": "https://billing.stripe.com/p/session/bps_TEST"})
    client = StripeClient(api_key="sk_test", http_client=_mock_client(handler))
    client.create_billing_portal_session(
        customer_id="cus_A", return_url="https://example.com/account",
    )
    assert seen["path"] == "/v1/billing_portal/sessions"


def test_get_subscription():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/subscriptions/sub_X"
        return httpx.Response(200, json={"id": "sub_X", "status": "active"})
    client = StripeClient(api_key="sk_test", http_client=_mock_client(handler))
    sub = client.get_subscription("sub_X")
    assert sub["status"] == "active"


def test_http_error_raises_stripe_error():
    def handler(request):
        return httpx.Response(402, json={"error": {"message": "card declined"}})
    client = StripeClient(api_key="sk_test", http_client=_mock_client(handler))
    with pytest.raises(StripeError, match="HTTP 402"):
        client.create_customer("x@y.com")


def test_api_base_uses_official_default():
    assert API_BASE.startswith("https://api.stripe.com")


# --------------------------------------------------- webhook verification


def _build_sig(secret: str, payload: bytes, ts: int) -> str:
    signed = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_verify_webhook_happy_path():
    payload = b'{"type":"customer.subscription.updated"}'
    now = 1700000000
    header = _build_sig("whsec_TEST", payload, now)
    event = StripeClient.verify_webhook(payload, header, secret="whsec_TEST", now=now)
    assert event["type"] == "customer.subscription.updated"


def test_verify_webhook_multiple_signatures_accepts_any_match():
    payload = b'{"type":"x"}'
    now = 1700000000
    signed = f"{now}.".encode() + payload
    good = hmac.new(b"whsec_TEST", signed, hashlib.sha256).hexdigest()
    header = f"t={now},v1=aaaa,v1={good}"
    event = StripeClient.verify_webhook(payload, header, secret="whsec_TEST", now=now)
    assert event["type"] == "x"


def test_verify_webhook_bad_signature_raises():
    payload = b'{"type":"x"}'
    now = 1700000000
    header = f"t={now},v1=deadbeef"
    with pytest.raises(StripeError, match="no matching v1"):
        StripeClient.verify_webhook(payload, header, secret="whsec_TEST", now=now)


def test_verify_webhook_outside_tolerance_raises():
    payload = b'{"type":"x"}'
    ts = 1700000000
    header = _build_sig("whsec_TEST", payload, ts)
    too_late = ts + DEFAULT_TOLERANCE_SECONDS + 60
    with pytest.raises(StripeError, match="outside tolerance"):
        StripeClient.verify_webhook(payload, header, secret="whsec_TEST", now=too_late)


def test_verify_webhook_missing_header_raises():
    with pytest.raises(StripeError, match="missing"):
        StripeClient.verify_webhook(b"{}", "", secret="whsec_TEST")


def test_verify_webhook_missing_secret_raises():
    with pytest.raises(StripeError, match=ENV_WEBHOOK_SECRET):
        StripeClient.verify_webhook(b"{}", "t=1,v1=x", secret=None)


def test_verify_webhook_malformed_header_raises():
    with pytest.raises(StripeError, match="malformed"):
        StripeClient.verify_webhook(b"{}", "garbage", secret="whsec_TEST")


def test_verify_webhook_non_integer_timestamp():
    with pytest.raises(StripeError, match="non-integer"):
        StripeClient.verify_webhook(b"{}", "t=abc,v1=aa", secret="whsec_TEST", now=0)


def test_verify_webhook_reads_env_var_default(monkeypatch):
    monkeypatch.setenv(ENV_WEBHOOK_SECRET, "whsec_ENV")
    payload = b'{"type":"ok"}'
    now = 1700000000
    header = _build_sig("whsec_ENV", payload, now)
    event = StripeClient.verify_webhook(payload, header, now=now)
    assert event["type"] == "ok"


def test_context_manager_closes_owned_client():
    with StripeClient(api_key="sk_test") as c:
        assert c._owns_client is True
    # No exception on exit


# --------------------------------------------------- client env-var resolution


def test_api_key_read_from_env(monkeypatch):
    monkeypatch.setenv(ENV_SECRET_KEY, "sk_env")
    client = StripeClient()
    assert client.api_key == "sk_env"
