"""Stripe router tests."""
import hmac
import hashlib
import json
import time

import httpx
import pytest

from edge_equation.auth.sessions import COOKIE_NAME
from edge_equation.auth.subscriptions import SubscriptionStore
from edge_equation.auth.tokens import AuthTokenStore
from edge_equation.auth.users import UserStore
from edge_equation.persistence.db import Database
from edge_equation.stripe_client import StripeClient


WEBHOOK_SECRET = "whsec_TEST"
STRIPE_SECRET = "sk_test_123"
PRICE_ID = "price_TEST"


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("WEBSITE_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("EE_COOKIE_SECURE", "false")
    monkeypatch.setenv("STRIPE_SECRET_KEY", STRIPE_SECRET)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_ID", PRICE_ID)


def _login(client, tmp_path, email="bob@example.com"):
    conn = Database.open(str(tmp_path / "s.db"))
    Database.migrate(conn)
    raw, _ = AuthTokenStore.mint(conn, email)
    conn.close()
    r = client.get("/auth/verify", params={"token": raw}, follow_redirects=False)
    return r.cookies[COOKIE_NAME]


def _inject_stripe_client(app, responder):
    """
    Replace the /stripe/* router's Stripe client factory with one backed by
    an httpx MockTransport. Returns the list of intercepted requests.
    """
    from api.routers import stripe_router
    calls = []

    def _handler(request):
        calls.append({
            "path": request.url.path,
            "method": request.method,
            "body": request.content.decode() if request.content else "",
            "headers": dict(request.headers),
        })
        return responder(request)

    def factory():
        return StripeClient(
            api_key=STRIPE_SECRET,
            http_client=httpx.Client(transport=httpx.MockTransport(_handler)),
        )

    app.dependency_overrides[stripe_router._stripe_client] = factory
    return calls


# ----------------------------------------------------------- checkout session


def test_checkout_requires_auth(client):
    r = client.post("/stripe/create-checkout-session")
    assert r.status_code == 401


def test_checkout_creates_customer_on_first_run(client, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")

    def responder(request):
        if request.url.path == "/v1/customers":
            return httpx.Response(200, json={"id": "cus_NEW", "email": "bob@example.com"})
        if request.url.path == "/v1/checkout/sessions":
            return httpx.Response(200, json={
                "id": "cs_TEST", "url": "https://checkout.stripe.com/c/pay/cs_TEST",
            })
        return httpx.Response(404)

    calls = _inject_stripe_client(client.app, responder)
    try:
        r = client.post("/stripe/create-checkout-session",
                        cookies={COOKIE_NAME: session_id})
    finally:
        client.app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.json()["url"].startswith("https://checkout.stripe.com/")
    # Both Stripe endpoints were hit
    paths = [c["path"] for c in calls]
    assert "/v1/customers" in paths
    assert "/v1/checkout/sessions" in paths

    # User now carries a stripe_customer_id
    conn = Database.open(str(tmp_path / "s.db"))
    u = UserStore.get_by_email(conn, "bob@example.com")
    assert u.stripe_customer_id == "cus_NEW"
    conn.close()


def test_checkout_reuses_existing_customer(client, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")
    conn = Database.open(str(tmp_path / "s.db"))
    u = UserStore.get_by_email(conn, "bob@example.com")
    UserStore.set_stripe_customer_id(conn, u.user_id, "cus_PRE_EXISTING")
    conn.close()

    def responder(request):
        if request.url.path == "/v1/customers":
            raise AssertionError("should not create a new customer")
        return httpx.Response(200, json={
            "id": "cs_TEST", "url": "https://checkout.stripe.com/c/pay/cs_TEST",
        })

    calls = _inject_stripe_client(client.app, responder)
    try:
        r = client.post("/stripe/create-checkout-session",
                        cookies={COOKIE_NAME: session_id})
    finally:
        client.app.dependency_overrides.clear()

    assert r.status_code == 200
    paths = [c["path"] for c in calls]
    assert "/v1/customers" not in paths


def test_checkout_stripe_error_bubbles(client, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")

    def responder(request):
        return httpx.Response(402, json={"error": {"message": "card declined"}})

    _inject_stripe_client(client.app, responder)
    try:
        r = client.post("/stripe/create-checkout-session",
                        cookies={COOKIE_NAME: session_id})
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 502
    assert "stripe" in r.json()["detail"].lower()


# ----------------------------------------------------------- portal session


def test_portal_requires_auth(client):
    r = client.post("/stripe/create-portal-session")
    assert r.status_code == 401


def test_portal_without_stripe_customer_id_400(client, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")
    r = client.post("/stripe/create-portal-session",
                    cookies={COOKIE_NAME: session_id})
    assert r.status_code == 400


def test_portal_returns_url_for_existing_customer(client, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")
    conn = Database.open(str(tmp_path / "s.db"))
    u = UserStore.get_by_email(conn, "bob@example.com")
    UserStore.set_stripe_customer_id(conn, u.user_id, "cus_Z")
    conn.close()

    def responder(request):
        return httpx.Response(200, json={
            "id": "bps_TEST",
            "url": "https://billing.stripe.com/p/session/bps_TEST",
        })
    _inject_stripe_client(client.app, responder)
    try:
        r = client.post("/stripe/create-portal-session",
                        cookies={COOKIE_NAME: session_id})
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json()["url"].startswith("https://billing.stripe.com/")


# ----------------------------------------------------------- webhook


def _sign(payload: bytes, secret: str, ts: int) -> str:
    signed = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_webhook_rejects_missing_signature(client):
    r = client.post("/stripe/webhook", content=b"{}")
    assert r.status_code == 400


def test_webhook_rejects_bad_signature(client):
    now = int(time.time())
    r = client.post("/stripe/webhook", content=b"{}",
                    headers={"Stripe-Signature": f"t={now},v1=deadbeef"})
    assert r.status_code == 400


def test_webhook_checkout_completed_activates_subscription(client, tmp_path):
    # Seed a user
    conn = Database.open(str(tmp_path / "s.db"))
    Database.migrate(conn)
    u = UserStore.find_or_create(conn, "bob@example.com")
    conn.close()

    event = {
        "id": "evt_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_1",
            "customer": "cus_NEW",
            "subscription": "sub_111",
            "metadata": {"user_id": str(u.user_id)},
        }},
    }
    body = json.dumps(event).encode()
    now = int(time.time())
    header = _sign(body, WEBHOOK_SECRET, now)

    r = client.post("/stripe/webhook", content=body,
                    headers={"Stripe-Signature": header,
                             "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["received"] is True

    conn = Database.open(str(tmp_path / "s.db"))
    assert SubscriptionStore.has_active(conn, u.user_id) is True
    # Stripe customer id was persisted on the user row
    assert UserStore.get_by_id(conn, u.user_id).stripe_customer_id == "cus_NEW"
    conn.close()


def test_webhook_subscription_updated_writes_status(client, tmp_path):
    conn = Database.open(str(tmp_path / "s.db"))
    Database.migrate(conn)
    u = UserStore.find_or_create(conn, "bob@example.com")
    UserStore.set_stripe_customer_id(conn, u.user_id, "cus_ABC")
    conn.close()

    event = {
        "id": "evt_2",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_XYZ",
            "customer": "cus_ABC",
            "status": "active",
            "current_period_end": 1800000000,  # unix timestamp
            "cancel_at_period_end": False,
        }},
    }
    body = json.dumps(event).encode()
    now = int(time.time())
    header = _sign(body, WEBHOOK_SECRET, now)
    r = client.post("/stripe/webhook", content=body,
                    headers={"Stripe-Signature": header,
                             "Content-Type": "application/json"})
    assert r.status_code == 200

    conn = Database.open(str(tmp_path / "s.db"))
    sub = SubscriptionStore.get_by_stripe_id(conn, "sub_XYZ")
    assert sub is not None
    assert sub.status == "active"
    conn.close()


def test_webhook_subscription_deleted_sets_canceled(client, tmp_path):
    conn = Database.open(str(tmp_path / "s.db"))
    Database.migrate(conn)
    u = UserStore.find_or_create(conn, "bob@example.com")
    UserStore.set_stripe_customer_id(conn, u.user_id, "cus_ABC")
    SubscriptionStore.upsert(conn, u.user_id, "sub_X", "active")
    conn.close()

    event = {
        "id": "evt_3",
        "type": "customer.subscription.deleted",
        "data": {"object": {
            "id": "sub_X", "customer": "cus_ABC", "status": "canceled",
        }},
    }
    body = json.dumps(event).encode()
    now = int(time.time())
    header = _sign(body, WEBHOOK_SECRET, now)
    r = client.post("/stripe/webhook", content=body,
                    headers={"Stripe-Signature": header,
                             "Content-Type": "application/json"})
    assert r.status_code == 200

    conn = Database.open(str(tmp_path / "s.db"))
    assert SubscriptionStore.has_active(conn, u.user_id) is False
    conn.close()


def test_webhook_unknown_event_type_accepted(client):
    body = json.dumps({"type": "some.other.event", "data": {"object": {}}}).encode()
    now = int(time.time())
    header = _sign(body, WEBHOOK_SECRET, now)
    r = client.post("/stripe/webhook", content=body,
                    headers={"Stripe-Signature": header,
                             "Content-Type": "application/json"})
    assert r.status_code == 200  # idempotent; don't 4xx unknown events
